<script setup>
import { Handle, Position } from "@vue-flow/core";
import { computed } from "vue";

const props = defineProps({
	id: { type: String, required: true },
	label: { type: String, default: "" },
	data: { type: Object, default: () => ({}) },
	selected: { type: Boolean, default: false },
});

const initial = computed(() => (props.label || props.id || "?").slice(0, 1).toUpperCase());
const agent = computed(() => props.data.agent || "unassigned");
</script>

<template>
	<div class="ngw-node" :class="{ selected: props.selected }">
		<Handle type="target" :position="Position.Left" />
		<div class="ngw-node-badge">{{ initial }}</div>
		<div class="ngw-node-text">
			<div class="ngw-node-title">{{ props.label || props.id }}</div>
			<div class="ngw-node-agent">{{ agent }}</div>
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
.ngw-node-title {
	font-weight: 600;
	font-size: var(--text-sm);
}
.ngw-node-agent {
	font-size: var(--text-xs);
	color: var(--text-muted);
}
</style>
