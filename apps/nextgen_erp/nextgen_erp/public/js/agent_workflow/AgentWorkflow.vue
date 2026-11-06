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
const triggers = ref([]);

const { addEdges, onConnect } = useVueFlow();

const selected = computed(() => nodes.value.find((node) => node.id === selectedId.value) || null);
const selectedKind = computed(() => selected.value?.data?.kind || "agent");
const agents = computed(() => doc.value?.available_agents || []);
const connections = computed(() => doc.value?.connections || []);
const kinds = computed(() => doc.value?.node_kinds || []);

// What a freshly dropped node of each kind starts as. Everything here is
// validated again on the server, which is what actually decides whether a graph
// may be saved.
const DEFAULTS = {
	"agent": () => ({ kind: "agent", agent: agents.value[0]?.key || "", prompt: "", model: "" }),
	"source.query": () => ({ kind: "source.query", doctype: "", fields: "name", filters: "", order_by: "modified desc", limit: 100 }),
	"source.excel": () => ({ kind: "source.excel", file: "", header_row: 1, limit: 500 }),
	"source.api": () => ({ kind: "source.api", connection: connections.value[0]?.name || "", path: "/", method: "GET", query: "", body: "" }),
	"sink.report": () => ({ kind: "sink.report", title: "", format: "html" }),
};

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

function toCanvasNode(node) {
	return {
		id: String(node.id),
		type: "agent",
		label: node.label || node.id,
		position: node.position || { x: 0, y: 0 },
		// A graph saved before typed nodes existed carries no kind; it is a
		// graph of agent nodes and stays one.
		data: { kind: "agent", ...(node.data || {}) },
	};
}

function applyGraph(graph) {
	nodes.value = (graph.nodes || []).map(toCanvasNode);
	edges.value = (graph.edges || []).map((edge, index) => ({
		id: edge.id || `e${index}`,
		source: String(edge.source),
		target: String(edge.target),
	}));
}

async function loadWorkflow() {
	doc.value = await call("get_workflow", { name: props.workflow });
	applyGraph(doc.value.graph);
	triggers.value = (doc.value.triggers || []).map((trigger) => ({ ...trigger }));
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

function addNode(kind) {
	const id = `node_${nodes.value.length + 1}_${Date.now().toString(36).slice(-4)}`;
	const build = DEFAULTS[kind] || DEFAULTS.agent;
	nodes.value.push({
		id,
		type: "agent",
		label: `${kind.split(".").pop()}_${nodes.value.length + 1}`,
		position: { x: 80 + nodes.value.length * 220, y: 160 },
		data: build(),
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

function pickFile() {
	// Frappe's own uploader, so the file becomes a File record with the usual
	// private-attachment rules; the node stores only its name.
	new frappe.ui.FileUploader({
		disable_file_browser: false,
		on_success: (file) => {
			if (!selected.value) return;
			selected.value.data.file = file.name || file.file_url;
			markDirty();
		},
	});
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

// ---------------------------------------------------------------- triggers

function addTrigger(type) {
	triggers.value.push({
		trigger_type: type,
		enabled: 0,
		run_as: frappe.session.user,
		cron: type === "Schedule" ? "0 8 * * *" : "",
		trigger_doctype: "",
		doc_event: "after_insert",
		condition: "",
	});
	markDirty();
}

function removeTrigger(index) {
	triggers.value.splice(index, 1);
	markDirty();
}

const webhookUrl = computed(() =>
	doc.value ? `${window.location.origin}${doc.value.webhook_url}` : "",
);
const webhookToken = ref("");

async function revealToken() {
	// The token is a credential, so it is fetched on request rather than
	// arriving with every load of this page.
	const result = await call("reveal_webhook_token", { workflow: doc.value.name });
	webhookToken.value = result.token;
}

// ---------------------------------------------------------------- actions

async function save() {
	saving.value = true;
	try {
		// The server validates the graph: known kinds, agents the policy leaves
		// able to work, no duplicate ids, no dangling edges, no cycles. A
		// rejection surfaces here unchanged.
		doc.value = await call("save_workflow", {
			name: doc.value.name,
			title: doc.value.title,
			description: doc.value.description,
			enabled: doc.value.enabled ? 1 : 0,
			graph: JSON.stringify(graphPayload()),
			triggers: JSON.stringify(triggers.value),
		});
		applyGraph(doc.value.graph);
		triggers.value = (doc.value.triggers || []).map((trigger) => ({ ...trigger }));
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
			applyGraph(parsed.graph || parsed);
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
					data: { kind: "agent", agent: "assistant", prompt: "" },
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
		const data = node.data || {};
		lines.push(`### ${index + 1}. ${node.label}`);
		lines.push(`- Kind: ${data.kind || "agent"}`);
		if ((data.kind || "agent") === "agent") {
			lines.push(`- Agent: ${data.agent || "(none)"}`);
			lines.push(`- Model: ${data.model || "Default model"}`);
			if (data.prompt) lines.push("", data.prompt);
		} else if (data.kind === "source.query") {
			lines.push(`- DocType: ${data.doctype || "(none)"}`);
			lines.push(`- Filters: ${data.filters || "(none)"}`);
		} else if (data.kind === "source.excel") {
			lines.push(`- File: ${data.file || "(none)"}`);
		} else if (data.kind === "source.api") {
			lines.push(`- Connection: ${data.connection || "(none)"}`);
			lines.push(`- Request: ${data.method || "GET"} ${data.path || "/"}`);
		} else if (data.kind === "sink.report") {
			lines.push(`- Format: ${data.format || "html"}`);
		}
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
						{{ workflows.length }}
						{{ __("workflows · data in, agents in the middle, a result out") }}
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
						<span>{{ item.node_count }} {{ __("nodes") }}</span>
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
					<button :class="{ active: tab === 'triggers' }" @click="tab = 'triggers'">
						{{ __("Triggers") }}
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
							<td class="text-muted">{{ run.trigger_type }}</td>
							<td class="text-muted">{{ run.started_at }}</td>
							<td class="text-muted">{{ run.error_summary }}</td>
						</tr>
						<tr v-if="!history.length">
							<td colspan="5" class="text-muted">{{ __("No runs yet") }}</td>
						</tr>
					</tbody>
				</table>
			</div>

			<div v-show="tab === 'canvas'" class="ngw-canvas-wrap">
				<aside class="ngw-panel">
					<h6>{{ __("Add node") }}</h6>
					<button
						v-for="kind in kinds"
						:key="kind.key"
						class="ngw-add"
						@click="addNode(kind.key)"
					>
						+ {{ kind.title }}
					</button>

					<template v-if="selected">
						<h6>{{ __("Node") }}</h6>
						<label>{{ __("Node name") }}</label>
						<input v-model="selected.label" class="form-control" @input="markDirty" />

						<!-- AI agent -->
						<template v-if="selectedKind === 'agent'">
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
						</template>

						<!-- ERP query -->
						<template v-else-if="selectedKind === 'source.query'">
							<label>{{ __("DocType") }}</label>
							<input v-model="selected.data.doctype" class="form-control" @input="markDirty" />
							<label>{{ __("Fields (comma separated)") }}</label>
							<input v-model="selected.data.fields" class="form-control" @input="markDirty" />
							<label>{{ __("Filters (JSON)") }}</label>
							<textarea
								v-model="selected.data.filters"
								class="form-control"
								rows="3"
								placeholder='{"status": "To Deliver"}'
								@input="markDirty"
							/>
							<label>{{ __("Order by") }}</label>
							<input v-model="selected.data.order_by" class="form-control" @input="markDirty" />
							<label>{{ __("Row limit") }}</label>
							<input
								v-model.number="selected.data.limit"
								type="number"
								class="form-control"
								@input="markDirty"
							/>
							<p class="text-muted ngw-hint">
								{{ __("Rows are read with your own ERP permissions and scoped to this workflow's company.") }}
							</p>
						</template>

						<!-- Spreadsheet -->
						<template v-else-if="selectedKind === 'source.excel'">
							<label>{{ __("File") }}</label>
							<div class="ngw-row">
								<input v-model="selected.data.file" class="form-control" @input="markDirty" />
								<button class="btn btn-default btn-sm" @click="pickFile">
									{{ __("Choose") }}
								</button>
							</div>
							<label>{{ __("Header row") }}</label>
							<input
								v-model.number="selected.data.header_row"
								type="number"
								min="1"
								class="form-control"
								@input="markDirty"
							/>
							<label>{{ __("Row limit") }}</label>
							<input
								v-model.number="selected.data.limit"
								type="number"
								class="form-control"
								@input="markDirty"
							/>
							<p class="text-muted ngw-hint">
								{{ __(".xlsx, .csv and .tsv are supported. The file is read with your own permissions each time the workflow runs.") }}
							</p>
						</template>

						<!-- API call -->
						<template v-else-if="selectedKind === 'source.api'">
							<label>{{ __("Connection") }}</label>
							<select v-model="selected.data.connection" class="form-control" @change="markDirty">
								<option v-for="item in connections" :key="item.name" :value="item.name">
									{{ item.name }}
								</option>
							</select>
							<p v-if="!connections.length" class="text-muted ngw-hint">
								{{ __("No connections yet. Create a NextGen Agent Connection for this company first.") }}
							</p>
							<label>{{ __("Method") }}</label>
							<select v-model="selected.data.method" class="form-control" @change="markDirty">
								<option value="GET">GET</option>
								<option value="POST">POST</option>
							</select>
							<label>{{ __("Path") }}</label>
							<input v-model="selected.data.path" class="form-control" @input="markDirty" />
							<label>{{ __("Query parameters (JSON)") }}</label>
							<textarea
								v-model="selected.data.query"
								class="form-control"
								rows="2"
								@input="markDirty"
							/>
							<label>{{ __("Body (JSON, POST only)") }}</label>
							<textarea
								v-model="selected.data.body"
								class="form-control"
								rows="3"
								:placeholder="__('Leave empty to forward the previous node\'s rows')"
								@input="markDirty"
							/>
							<p class="text-muted ngw-hint">
								{{ __("The host and credentials belong to the connection, never to this graph. Only paths the connection allows can be called.") }}
							</p>
						</template>

						<!-- Report -->
						<template v-else-if="selectedKind === 'sink.report'">
							<label>{{ __("Report title") }}</label>
							<input v-model="selected.data.title" class="form-control" @input="markDirty" />
							<label>{{ __("Format") }}</label>
							<select v-model="selected.data.format" class="form-control" @change="markDirty">
								<option value="html">HTML</option>
								<option value="csv">CSV</option>
								<option value="json">JSON</option>
							</select>
							<p class="text-muted ngw-hint">
								{{ __("The report is attached to the run as a private file.") }}
							</p>
						</template>

						<button class="btn btn-danger btn-sm ngw-delete" @click="deleteNode">
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

			<div v-show="tab === 'triggers'" class="ngw-pane">
				<p class="text-muted">
					{{ __("Manual execution from this page always works. A trigger is for starting the workflow without anybody watching — so it must name the person whose ERP access it runs with.") }}
				</p>
				<div class="ngw-actions ngw-trigger-actions">
					<button class="btn btn-default btn-sm" @click="addTrigger('Schedule')">
						+ {{ __("Schedule") }}
					</button>
					<button class="btn btn-default btn-sm" @click="addTrigger('Webhook')">
						+ {{ __("Webhook") }}
					</button>
					<button class="btn btn-default btn-sm" @click="addTrigger('Document Event')">
						+ {{ __("Document Event") }}
					</button>
				</div>

				<div v-for="(trigger, index) in triggers" :key="index" class="ngw-trigger">
					<div class="ngw-trigger-head">
						<strong>{{ trigger.trigger_type }}</strong>
						<label class="ngw-check">
							<input type="checkbox" v-model="trigger.enabled" :true-value="1" :false-value="0" @change="markDirty" />
							{{ __("Enabled") }}
						</label>
						<button class="btn btn-danger btn-xs" @click="removeTrigger(index)">
							{{ __("Remove") }}
						</button>
					</div>
					<label>{{ __("Run as") }}</label>
					<input v-model="trigger.run_as" class="form-control" @input="markDirty" />

					<template v-if="trigger.trigger_type === 'Schedule'">
						<label>{{ __("Cron") }}</label>
						<input v-model="trigger.cron" class="form-control" placeholder="0 8 * * *" @input="markDirty" />
						<p class="text-muted ngw-hint">
							{{ __("Evaluated every five minutes, against the trigger's own last run.") }}
							<span v-if="trigger.next_run_at">
								{{ __("Next") }}: {{ trigger.next_run_at }}
							</span>
						</p>
					</template>

					<template v-else-if="trigger.trigger_type === 'Document Event'">
						<label>{{ __("DocType") }}</label>
						<input v-model="trigger.trigger_doctype" class="form-control" @input="markDirty" />
						<label>{{ __("Event") }}</label>
						<select v-model="trigger.doc_event" class="form-control" @change="markDirty">
							<option value="after_insert">after_insert</option>
							<option value="on_update">on_update</option>
							<option value="on_submit">on_submit</option>
							<option value="on_cancel">on_cancel</option>
						</select>
						<label>{{ __("Condition (JSON, optional)") }}</label>
						<input
							v-model="trigger.condition"
							class="form-control"
							placeholder='{"status": "To Deliver"}'
							@input="markDirty"
						/>
					</template>

					<template v-else>
						<label>{{ __("Endpoint") }}</label>
						<input class="form-control" :value="webhookUrl" readonly />
						<label>{{ __("Token") }}</label>
						<div class="ngw-row">
							<input
								class="form-control"
								:value="webhookToken || '••••••••'"
								readonly
							/>
							<button class="btn btn-default btn-sm" @click="revealToken">
								{{ __("Reveal") }}
							</button>
						</div>
						<p class="text-muted ngw-hint">
							{{ __("POST here with the token in the X-NextGen-Workflow-Token header. The token is generated on save; revealing it is a System Manager action.") }}
						</p>
					</template>
				</div>
				<p v-if="!triggers.length" class="text-muted ngw-hint">
					{{ __("No triggers. This workflow runs only when somebody executes it.") }}
				</p>
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
.ngw-panel label {
	display: block;
	margin-top: 10px;
	font-size: var(--text-sm);
	color: var(--text-muted);
}
.ngw-add {
	width: 100%;
	padding: 8px 10px;
	margin-bottom: 6px;
	border-radius: 8px;
	border: 1px dashed var(--border-color);
	background: none;
	color: var(--text-color);
	text-align: left;
}
.ngw-add:hover {
	border-color: var(--primary);
}
.ngw-row {
	display: flex;
	gap: 6px;
}
.ngw-hint {
	font-size: var(--text-xs);
	margin-top: 10px;
}
.ngw-delete {
	margin-top: 16px;
}
.ngw-canvas {
	flex: 1;
	min-width: 0;
	border: 1px solid var(--border-color);
	border-radius: 10px;
	overflow: hidden;
}
.ngw-trigger-actions {
	margin: 12px 0;
}
.ngw-trigger {
	border: 1px solid var(--border-color);
	border-radius: 10px;
	padding: 12px 14px;
	margin-bottom: 12px;
	max-width: 620px;
}
.ngw-trigger-head {
	display: flex;
	gap: 16px;
	align-items: center;
	justify-content: space-between;
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
