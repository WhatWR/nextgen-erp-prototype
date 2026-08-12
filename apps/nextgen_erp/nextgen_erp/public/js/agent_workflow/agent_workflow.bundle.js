/* global frappe, $ */

// Mirrors frappe/public/js/workflow_builder: a Desk Page mounts a Vue app that
// owns the canvas. @vue-flow resolves through esbuild's NODE_PATHS, which
// include every installed app's node_modules.
import { createApp } from "vue";

import AgentWorkflow from "./AgentWorkflow.vue";

class AgentWorkflowDesigner {
	constructor({ wrapper, page, workflow }) {
		this.$wrapper = $(wrapper);
		this.page = page;
		this.workflow = workflow;
		this.setup_page();
		this.setup_app();
	}

	setup_page() {
		// The page skeleton comes from on_page_load. Guard anyway so a missing
		// one degrades to a working canvas rather than a blank screen.
		if (!this.page) return;
		this.page.clear_custom_actions();
		this.page.set_title(this.workflow ? __("Workflow Designer") : __("Agent Workflows"));
	}

	setup_app() {
		const target = this.$wrapper.get(0);
		if (!target) {
			frappe.msgprint(__("Could not find a container to render the workflow designer."));
			return;
		}
		const app = createApp(AgentWorkflow, { workflow: this.workflow, page: this.page });
		app.config.globalProperties.__ = window.__;
		// A render error would otherwise leave a silent blank page.
		app.config.errorHandler = (error) => {
			console.error("Agent workflow designer", error);
			frappe.msgprint(__("Workflow designer error: {0}", [error.message || error]));
		};
		this.$app = app.mount(target);
	}
}

frappe.provide("frappe.ui");
frappe.ui.AgentWorkflowDesigner = AgentWorkflowDesigner;
export default AgentWorkflowDesigner;
