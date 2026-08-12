/* global frappe, $ */

// Route: /app/agent-workflow            -> workflow list
//        /app/agent-workflow/WF-00001   -> designer for that workflow
//
// on_page_load builds the page skeleton. Without it there is no `wrapper.page`
// and no `.layout-main-section` to mount the Vue app into, and the page renders
// blank.
frappe.pages["agent-workflow"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Agent Workflows"),
		single_column: true,
	});

	if (frappe.boot.developer_mode) {
		frappe.hot_update = frappe.hot_update || [];
		frappe.hot_update.push(() => load_agent_workflow(wrapper));
	}
};

frappe.pages["agent-workflow"].on_page_show = function (wrapper) {
	load_agent_workflow(wrapper);
};

function load_agent_workflow(wrapper) {
	const route = frappe.get_route();
	const $parent = $(wrapper).find(".layout-main-section");
	$parent.empty();

	frappe.require("agent_workflow.bundle.js").then(() => {
		frappe.agent_workflow = new frappe.ui.AgentWorkflowDesigner({
			wrapper: $parent,
			page: wrapper.page,
			workflow: route.length > 1 ? route[1] : null,
		});
	});
}
