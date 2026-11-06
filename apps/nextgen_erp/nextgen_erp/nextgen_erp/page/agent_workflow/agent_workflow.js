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
	// Loading affordance: the bundle can take a moment on first load; never
	// leave the page blank. The Vue app replaces this node when it mounts.
	$parent.html(`
		<div class="ng-wf-loading" role="status" aria-live="polite">
			<span class="ng-wf-spinner" aria-hidden="true"></span>
			<span>${__("Loading workflow designer...")}</span>
		</div>
		<style>
			.ng-wf-loading { display: flex; align-items: center; justify-content: center; gap: 10px; min-height: 240px; color: var(--text-muted); font-size: 13px; }
			.ng-wf-spinner { width: 22px; height: 22px; border: 3px solid var(--border-color); border-top-color: var(--primary); border-radius: 50%; animation: ng-wf-spin .8s linear infinite; }
			@keyframes ng-wf-spin { to { transform: rotate(360deg); } }
			@media (prefers-reduced-motion: reduce) { .ng-wf-spinner { animation: none; } }
		</style>
	`);

	// Loaded by path, not by bundle name. A bundle-name lookup goes through the
	// volume's assets.json, which is seeded once and does not track later image
	// builds; the stable copy in the app's public/dist always matches the image.
	frappe
		.require("/assets/nextgen_erp/dist/js/agent_workflow.bundle.js")
		.then(() => {
			if (!frappe.ui.AgentWorkflowDesigner) {
				frappe.msgprint(
					__("The workflow designer assets are missing. Rebuild the image, or run: bench build --app nextgen_erp"),
				);
				return;
			}
			frappe.agent_workflow = new frappe.ui.AgentWorkflowDesigner({
				wrapper: $parent,
				page: wrapper.page,
				workflow: route.length > 1 ? route[1] : null,
			});
		})
		.catch((error) => {
			console.error("Agent workflow bundle failed to load", error);
			frappe.msgprint(__("Could not load the workflow designer assets."));
		});
}
