<script setup>
/* global frappe */
import { Background } from "@vue-flow/background";
import { VueFlow, useVueFlow } from "@vue-flow/core";
import { computed, onMounted, ref, watch } from "vue";

import AgentNode from "./AgentNode.vue";

const GATEWAY = "nextgen_erp.agent_gateway.api.";

const props = defineProps({
	workflow: { type: String, default: null },
	page: { type: Object, default: null },
});

const loading = ref(true);
const saving = ref(false);
const dirty = ref(false);
const tab = ref("canvas");
const doc = ref(null);
const workflows = ref([]);
const history = ref([]);
const selectedId = ref(null);
const nodes = ref([]);
const edges = ref([]);

const { addEdges, project, onConnect } = useVueFlow();

const selected = computed(() => nodes.value.find((node) => node.id === selectedId.value) || null);
const agents = computed(() => doc.value?.available_agents || []);

function call(method, args = {}) {
	return frappe.call({ method: GATEWAY + method, args }).then((response) => response.message);
}

function markDirty() {
	dirty.value = true;
}

// ---------------------------------------------------------------- loading

async function loadList() {
	workflows.value = await call("list_workflows");
	loading.value = false;
}

async function loadWorkflow() {
	doc.value = await call("get_workflow", { name: props.workflow });
	nodes.value = (doc.value.graph.nodes || []).map((node) => ({
		id: String(node.id),
		type: "agent",
		label: node.label || node.id,
		position: node.position || { x: 0, y: 0 },
		data: { ...(node.data || {}) },
	}));
	edges.value = (doc.value.graph.edges || []).map((edge, index) => ({
		id: edge.id || `e${index}`,
		source: String(edge.source),
		target: String(edge.target),
	}));
	history.value = await call("get_workflow_history", { workflow: props.workflow });
	dirty.value = false;
	loading.value = false;
}

onMounted(() => (props.workflow ? loadWorkflow() : loadList()));
watch(() => props.workflow, () => (props.workflow ? loadWorkflow() : loadList()));

// ---------------------------------------------------------------- editing

onConnect((connection) => {
	addEdges([{ ...connection, id: `e-${connection.source}-${connection.target}` }]);
	markDirty();
});

function addNode() {
	const id = `node_${nodes.value.length + 1}_${Date.now().toString(36).slice(-4)}`;
	nodes.value.push({
		id,
		type: "agent",
		label: `agent_${nodes.value.length + 1}`,
		position: { x: 80 + nodes.value.length * 220, y: 160 },
		data: { agent: agents.value[0]?.key || "", prompt: "", model: "" },
	});
	selectedId.value = id;
	markDirty();
}

function deleteNode() {
	if (!selected.value) return;
	const id = selected.value.id;
	nodes.value = nodes.value.filter((node) => node.id !== id);
	edges.value = edges.value.filter((edge) => edge.source !== id && edge.target !== id);
	selectedId.value = null;
	markDirty();
}

function graphPayload() {
	return {
		nodes: nodes.value.map((node) => ({
			id: node.id,
			type: "agent",
			label: node.label,
			position: node.position,
			data: node.data,
		})),
		edges: edges.value.map((edge) => ({
			id: edge.id,
			source: edge.source,
			target: edge.target,
		})),
	};
}

// ---------------------------------------------------------------- actions

async function save() {
	saving.value = true;
	try {
		// The server validates the graph: known agents, no duplicate ids, no
		// dangling edges, no cycles. A rejection surfaces here unchanged.
		doc.value = await call("save_workflow", {
			name: doc.value.name,
			title: doc.value.title,
			description: doc.value.description,
			enabled: doc.value.enabled ? 1 : 0,
			graph: JSON.stringify(graphPayload()),
		});
		dirty.value = false;
		frappe.show_alert({ message: __("Workflow saved"), indicator: "green" });
	} finally {
		saving.value = false;
	}
}

async function execute() {
	if (dirty.value) {
		frappe.msgprint(__("Save the workflow before running it."));
		return;
	}
	const result = await call("start_workflow_run", { workflow: doc.value.name });
	frappe.show_alert({ message: __("Run queued: {0}", [result.run_id]), indicator: "blue" });
	history.value = await call("get_workflow_history", { workflow: doc.value.name });
}

function exportGraph() {
	const payload = JSON.stringify(
		{ title: doc.value.title, description: doc.value.description, graph: graphPayload() },
		null,
		2,
	);
	const blob = new Blob([payload], { type: "application/json" });
	const link = document.createElement("a");
	link.href = URL.createObjectURL(blob);
	link.download = `${doc.value.name}.json`;
	link.click();
	URL.revokeObjectURL(link.href);
}

function importGraph() {
	const input = document.createElement("input");
	input.type = "file";
	input.accept = "application/json";
	input.onchange = async (event) => {
		const file = event.target.files[0];
		if (!file) return;
		try {
			const parsed = JSON.parse(await file.text());
			const graph = parsed.graph || parsed;
			nodes.value = (graph.nodes || []).map((node) => ({
				id: String(node.id),
				type: "agent",
				label: node.label || node.id,
				position: node.position || { x: 0, y: 0 },
				data: { ...(node.data || {}) },
			}));
			edges.value = (graph.edges || []).map((edge, index) => ({
				id: edge.id || `e${index}`,
				source: String(edge.source),
				target: String(edge.target),
			}));
			markDirty();
		} catch (error) {
			frappe.msgprint(__("That file is not a valid workflow export."));
		}
	};
	input.click();
}

async function createWorkflow() {
	const created = await call("save_workflow", {
		title: __("New Workflow"),
		graph: JSON.stringify({
			nodes: [
				{
					id: "node_1",
					type: "agent",
					label: "agent_1",
					position: { x: 120, y: 160 },
					data: { agent: "assistant", prompt: "" },
				},
			],
			edges: [],
		}),
	});
	frappe.set_route("agent-workflow", created.name);
}

// The markdown view is a read-only rendering of the same graph.
const markdown = computed(() => {
	if (!doc.value) return "";
	const lines = [`# ${doc.value.title}`, "", doc.value.description || "", "", "## Nodes", ""];
	nodes.value.forEach((node, index) => {
		lines.push(`### ${index + 1}. ${node.label}`);
		lines.push(`- Agent: ${node.data.agent || "(none)"}`);
		lines.push(`- Model: ${node.data.model || "Default model"}`);
		if (node.data.prompt) lines.push("", node.data.prompt);
		lines.push("");
	});
	if (edges.value.length) {
		lines.push("## Flow", "");
		edges.value.forEach((edge) => lines.push(`- ${edge.source} → ${edge.target}`));
	}
	return lines.join("\n");
});
</script>

<template>
	<div class="ngw-root">
		<div v-if="loading" class="ngw-empty">{{ __("Loading…") }}</div>

		<!-- List -->
		<div v-else-if="!props.workflow" class="ngw-list">
			<div class="ngw-list-head">
				<div>
					<h3>{{ __("Agent Workflows") }}</h3>
					<p class="text-muted">
						{{ workflows.length }} {{ __("workflows · orchestrate multi-agent collaboration") }}
					</p>
				</div>
				<button class="btn btn-primary btn-sm" @click="createWorkflow">
					{{ __("Create Workflow") }}
				</button>
			</div>
			<div class="ngw-cards">
				<div
					v-for="item in workflows"
					:key="item.name"
					class="ngw-card"
					@click="frappe.set_route('agent-workflow', item.name)"
				>
					<div class="ngw-card-title">{{ item.title }}</div>
					<div class="ngw-card-desc">{{ item.description }}</div>
					<div class="ngw-card-meta">
						<span>{{ item.node_count }} {{ __("agents") }}</span>
						<span :class="item.enabled ? 'ngw-on' : 'ngw-off'">
							{{ item.enabled ? __("Enabled") : __("Disabled") }}
						</span>
						<span>{{ item.total_runs }} {{ __("runs") }}</span>
					</div>
				</div>
				<div v-if="!workflows.length" class="ngw-empty">
					{{ __("No workflows yet. Create one to orchestrate several agents.") }}
				</div>
			</div>
		</div>

		<!-- Designer -->
		<div v-else class="ngw-designer">
			<div class="ngw-toolbar">
				<div class="ngw-tabs">
					<button :class="{ active: tab === 'overview' }" @click="tab = 'overview'">
						{{ __("Overview") }}
					</button>
					<button :class="{ active: tab === 'canvas' }" @click="tab = 'canvas'">
						{{ __("Canvas") }}
					</button>
					<button :class="{ active: tab === 'md' }" @click="tab = 'md'">MD</button>
				</div>
				<div class="ngw-actions">
					<span v-if="dirty" class="ngw-dirty">{{ __("Unsaved") }}</span>
					<button class="btn btn-primary btn-sm" :disabled="saving" @click="save">
						{{ __("Save") }}
					</button>
					<button class="btn btn-default btn-sm" @click="execute">{{ __("Execute") }}</button>
					<button class="btn btn-default btn-sm" @click="exportGraph">{{ __("Export") }}</button>
					<button class="btn btn-default btn-sm" @click="importGraph">{{ __("Import") }}</button>
				</div>
			</div>

			<div v-show="tab === 'overview'" class="ngw-pane">
				<label>{{ __("Workflow name") }}</label>
				<input v-model="doc.title" class="form-control" @input="markDirty" />
				<label>{{ __("Description") }}</label>
				<textarea v-model="doc.description" class="form-control" rows="3" @input="markDirty" />
				<label class="ngw-check">
					<input type="checkbox" v-model="doc.enabled" @change="markDirty" />
					{{ __("Enabled (a disabled workflow can be edited but never executed)") }}
				</label>
				<div class="ngw-meta">
					{{ __("Company") }}: {{ doc.company }} · {{ __("Graph version") }}:
					{{ doc.graph_version }}
				</div>

				<h5>{{ __("Run history") }}</h5>
				<table class="table table-sm">
					<tbody>
						<tr v-for="run in history" :key="run.name">
							<td>
								<a href="#" @click.prevent="frappe.set_route('Form', 'NextGen Agent Run', run.name)">
									{{ run.name }}
								</a>
							</td>
							<td>{{ run.status }}</td>
							<td class="text-muted">{{ run.started_at }}</td>
							<td class="text-muted">{{ run.error_summary }}</td>
						</tr>
						<tr v-if="!history.length">
							<td colspan="4" class="text-muted">{{ __("No runs yet") }}</td>
						</tr>
					</tbody>
				</table>
			</div>

			<div v-show="tab === 'canvas'" class="ngw-canvas-wrap">
				<aside class="ngw-panel">
					<h6>{{ __("Node panel") }}</h6>
					<button class="ngw-add" @click="addNode">+ {{ __("Agent node") }}</button>

					<template v-if="selected">
						<h6>{{ __("Node") }}</h6>
						<label>{{ __("Node name") }}</label>
						<input v-model="selected.label" class="form-control" @input="markDirty" />
						<label>{{ __("Select agent") }}</label>
						<select v-model="selected.data.agent" class="form-control" @change="markDirty">
							<option v-for="agent in agents" :key="agent.key" :value="agent.key">
								{{ agent.title }}
							</option>
						</select>
						<label>{{ __("Node prompt (optional)") }}</label>
						<textarea
							v-model="selected.data.prompt"
							class="form-control"
							rows="6"
							@input="markDirty"
						/>
						<p class="text-muted ngw-hint">
							{{ __("The agent decides which tools to call. What it may actually do stays governed by this company's automation policy.") }}
						</p>
						<button class="btn btn-danger btn-sm" @click="deleteNode">
							{{ __("Delete node") }}
						</button>
					</template>
					<p v-else class="text-muted ngw-hint">{{ __("Select a node to edit it.") }}</p>
				</aside>

				<div class="ngw-canvas">
					<VueFlow
						v-model:nodes="nodes"
						v-model:edges="edges"
						:default-viewport="{ zoom: 0.9 }"
						fit-view-on-init
						@node-click="selectedId = $event.node.id"
						@node-drag-stop="markDirty"
					>
						<template #node-agent="nodeProps">
							<AgentNode v-bind="nodeProps" :selected="nodeProps.id === selectedId" />
						</template>
						<Background />
					</VueFlow>
				</div>
			</div>

			<div v-show="tab === 'md'" class="ngw-pane">
				<pre class="ngw-md">{{ markdown }}</pre>
			</div>
		</div>
	</div>
</template>

<style scoped>
.ngw-root {
	height: calc(100vh - var(--navbar-height) - 60px);
	display: flex;
	flex-direction: column;
}
.ngw-empty {
	padding: 40px;
	color: var(--text-muted);
}
.ngw-list-head {
	display: flex;
	justify-content: space-between;
	align-items: flex-start;
	margin-bottom: 16px;
}
.ngw-cards {
	display: grid;
	grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
	gap: 14px;
}
.ngw-card {
	border: 1px solid var(--border-color);
	border-radius: 10px;
	padding: 14px;
	cursor: pointer;
	background: var(--card-bg, var(--fg-color));
}
.ngw-card:hover {
	border-color: var(--primary);
}
.ngw-card-title {
	font-weight: 600;
}
.ngw-card-desc {
	color: var(--text-muted);
	font-size: var(--text-sm);
	margin: 6px 0 12px;
	min-height: 34px;
}
.ngw-card-meta {
	display: flex;
	gap: 12px;
	font-size: var(--text-xs);
	color: var(--text-muted);
}
.ngw-on {
	color: var(--green-600);
}
.ngw-off {
	color: var(--text-muted);
}
.ngw-toolbar {
	display: flex;
	justify-content: space-between;
	align-items: center;
	padding-bottom: 10px;
	border-bottom: 1px solid var(--border-color);
}
.ngw-tabs button {
	background: none;
	border: none;
	padding: 6px 12px;
	border-radius: 6px;
	color: var(--text-muted);
}
.ngw-tabs button.active {
	background: var(--bg-light-gray, var(--control-bg));
	color: var(--text-color);
	font-weight: 600;
}
.ngw-actions {
	display: flex;
	gap: 8px;
	align-items: center;
}
.ngw-dirty {
	color: var(--orange-500, #d97706);
	font-size: var(--text-xs);
}
.ngw-designer {
	display: flex;
	flex-direction: column;
	flex: 1;
	min-height: 0;
}
.ngw-pane {
	padding: 16px 4px;
	overflow: auto;
}
.ngw-pane label {
	display: block;
	margin-top: 12px;
	font-size: var(--text-sm);
	color: var(--text-muted);
}
.ngw-check {
	display: flex;
	gap: 8px;
	align-items: center;
}
.ngw-meta {
	margin: 12px 0 24px;
	color: var(--text-muted);
	font-size: var(--text-sm);
}
.ngw-canvas-wrap {
	display: flex;
	flex: 1;
	min-height: 0;
	gap: 12px;
}
.ngw-panel {
	width: 290px;
	flex-shrink: 0;
	overflow: auto;
	padding-right: 8px;
	border-right: 1px solid var(--border-color);
}
.ngw-panel h6 {
	margin-top: 16px;
	text-transform: uppercase;
	font-size: var(--text-xs);
	color: var(--text-muted);
}
.ngw-add {
	width: 100%;
	padding: 10px;
	border-radius: 8px;
	border: 1px dashed var(--border-color);
	background: none;
	color: var(--text-color);
}
.ngw-hint {
	font-size: var(--text-xs);
	margin-top: 10px;
}
.ngw-canvas {
	flex: 1;
	min-width: 0;
	border: 1px solid var(--border-color);
	border-radius: 10px;
	overflow: hidden;
}
.ngw-md {
	white-space: pre-wrap;
	font-family: var(--font-stack-mono);
	font-size: var(--text-sm);
}
</style>

<style>
@import "@vue-flow/core/dist/style.css";
@import "@vue-flow/core/dist/theme-default.css";
</style>
