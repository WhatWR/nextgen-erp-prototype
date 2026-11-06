<script setup>
import { Handle, Position } from "@vue-flow/core";
import { computed } from "vue";

const props = defineProps({
	id: { type: String, required: true },
	label: { type: String, default: "" },
	data: { type: Object, default: () => ({}) },
	selected: { type: Boolean, default: false },
});

// Icon and colour come from the node's kind, so the shape of a workflow is
// readable at a glance: where data enters, where a model thinks, where it lands.
const KINDS = {
	"agent": { icon: "◆", tone: "agent" },
	"source.query": { icon: "▤", tone: "source" },
	"source.excel": { icon: "▦", tone: "source" },
	"source.api": { icon: "⇄", tone: "source" },
	"sink.report": { icon: "▣", tone: "sink" },
};

const kind = computed(() => props.data.kind || "agent");
const style = computed(() => KINDS[kind.value] || KINDS.agent);

const subtitle = computed(() => {
	const data = props.data;
	switch (kind.value) {
		case "source.query":
			return data.doctype || __("no DocType");
		case "source.excel":
			return data.file || __("no file");
		case "source.api":
			return [data.connection, data.path].filter(Boolean).join(" ") || __("no connection");
		case "sink.report":
			return (data.format || "html").toUpperCase();
		default:
			return data.agent || __("unassigned");
	}
});
</script>

<template>
	<div class="ngw-node" :class="[style.tone, { selected: props.selected }]">
		<Handle type="target" :position="Position.Left" />
		<div class="ngw-node-badge">{{ style.icon }}</div>
		<div class="ngw-node-text">
			<div class="ngw-node-title">{{ props.label || props.id }}</div>
			<div class="ngw-node-agent">{{ subtitle }}</div>
		</div>
		<Handle type="source" :position="Position.Right" />
	</div>
</template>

<style scoped>
.ngw-node {
	display: flex;
	align-items: center;
	gap: 10px;
	min-width: 190px;
	padding: 10px 14px;
	border-radius: 10px;
	border: 1px solid var(--border-color);
	background: var(--card-bg, var(--fg-color));
	box-shadow: var(--shadow-base);
}
.ngw-node.selected {
	border-color: var(--primary);
	box-shadow: 0 0 0 2px var(--primary-color, var(--primary));
}
.ngw-node-badge {
	width: 26px;
	height: 26px;
	border-radius: 50%;
	display: grid;
	place-items: center;
	font-weight: 600;
	font-size: 12px;
	color: white;
	background: var(--primary);
	flex-shrink: 0;
}
.ngw-node.source .ngw-node-badge {
	background: var(--blue-500, #2490ef);
}
.ngw-node.sink .ngw-node-badge {
	background: var(--green-600, #0e9f6e);
}
.ngw-node-title {
	font-weight: 600;
	font-size: var(--text-sm);
}
.ngw-node-agent {
	font-size: var(--text-xs);
	color: var(--text-muted);
	max-width: 180px;
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
}
</style>
