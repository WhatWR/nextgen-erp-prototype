/* global frappe, $ */

frappe.pages["po-email-intake"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: __("PO Email Intake"),
		single_column: true,
	});
	wrapper.po_email_intake = new NextGenPOEmailIntake(wrapper);
};

frappe.pages["po-email-intake"].on_page_show = function (wrapper) {
	wrapper.po_email_intake?.refresh();
};

class NextGenPOEmailIntake {
	constructor(wrapper) {
		this.wrapper = wrapper;
		this.page = wrapper.page;
		this.tab = "queue";
		this.data = null;
		this.render_shell();
		this.page.set_primary_action(__("Sync Inbox"), () => this.sync_inbox(), "refresh");
		this.page.set_secondary_action(__("Refresh"), () => this.refresh());
		this.refresh();
	}

	api(method, args = {}) {
		return frappe.xcall(`nextgen_erp.purchase_email.api.${method}`, args);
	}

	escape(value) {
		return frappe.utils.escape_html(value == null ? "" : String(value));
	}

	money(value) {
		return new Intl.NumberFormat("th-TH", {
			style: "currency",
			currency: "THB",
			maximumFractionDigits: 2,
		}).format(Number(value) || 0);
	}

	render_shell() {
		this.$root = $(`
			<div class="ng-po-email">
				<section class="ng-po-hero">
					<div>
						<div class="ng-po-eyebrow">PROCUREMENT AUTOMATION</div>
						<h2>${__("PO Email Intake")}</h2>
						<p>${__("Connect a purchase mailbox, mirror Excel validation rules, and confirm AI drafts before ERP export.")}</p>
					</div>
					<div class="ng-po-hero-actions">
						<button type="button" class="ng-po-hero-btn" data-tab="connect">${__("1. Connect mailbox")}</button>
						<button type="button" class="ng-po-hero-btn" data-tab="rules">${__("2. Validation rules")}</button>
						<button type="button" class="ng-po-hero-btn" data-tab="queue">${__("3. Review queue")}</button>
					</div>
				</section>
				<div class="ng-po-kpi" data-role="kpi"></div>
				<nav class="ng-po-tabs" role="tablist">
					<button type="button" class="ng-po-tab" data-tab="connect">${__("Connect")}</button>
					<button type="button" class="ng-po-tab" data-tab="rules">${__("Rules")}</button>
					<button type="button" class="ng-po-tab is-active" data-tab="queue">${__("Review Queue")}</button>
				</nav>
				<section data-role="panel"></section>
			</div>
		`).appendTo($(this.wrapper).find(".layout-main-section").empty());

		this.$root.on("click", "[data-tab]", (event) => {
			this.tab = $(event.currentTarget).data("tab");
			this.render();
		});
		this.$root.on("click", "[data-action]", (event) => this.handle_action(event));
		this.$root.on("submit", "[data-form]", (event) => {
			event.preventDefault();
			this.handle_form(event);
		});
	}

	async refresh() {
		this.$root.addClass("is-loading");
		try {
			this.data = await this.api("get_overview");
			this.render();
		} catch (error) {
			frappe.msgprint({
				title: __("PO Email Intake"),
				message: error.message || __("Could not load overview"),
				indicator: "red",
			});
			throw error;
		} finally {
			this.$root.removeClass("is-loading");
		}
	}

	async sync_inbox() {
		this.$root.addClass("is-loading");
		try {
			const result = await this.api("sync_inbox", { limit: 20 });
			frappe.show_alert({ message: result.note || __("Inbox synced"), indicator: "green" });
			await this.refresh();
			this.tab = "queue";
			this.render();
		} catch (error) {
			frappe.msgprint(error.message || __("Sync failed"));
		} finally {
			this.$root.removeClass("is-loading");
		}
	}

	badge_class(status) {
		const map = {
			"Needs Review": "review",
			Flagged: "flagged",
			Approved: "approved",
			Rejected: "rejected",
			Exported: "exported",
			Draft: "review",
		};
		return map[status] || "review";
	}

	status_chip(status) {
		if (status === "Connected") return `<span class="ng-po-status-chip">${this.escape(status)}</span>`;
		if (status === "Needs Auth") return `<span class="ng-po-status-chip is-warn">${this.escape(status)}</span>`;
		if (status === "Error") return `<span class="ng-po-status-chip is-error">${this.escape(status)}</span>`;
		return `<span class="ng-po-status-chip is-muted">${this.escape(status || "Not Connected")}</span>`;
	}

	render() {
		if (!this.data) return;
		const counts = this.data.counts || {};
		this.$root.find("[data-role='kpi']").html(`
			<article><small>${__("Needs Review")}</small><strong>${counts["Needs Review"] || 0}</strong></article>
			<article><small>${__("Flagged")}</small><strong>${counts.Flagged || 0}</strong></article>
			<article><small>${__("Approved")}</small><strong>${counts.Approved || 0}</strong></article>
			<article><small>${__("Exported")}</small><strong>${counts.Exported || 0}</strong></article>
		`);
		this.$root.find(".ng-po-tab").removeClass("is-active");
		this.$root.find(`.ng-po-tab[data-tab='${this.tab}']`).addClass("is-active");

		const panel = this.$root.find("[data-role='panel']");
		if (this.tab === "connect") panel.html(this.render_connect());
		else if (this.tab === "rules") panel.html(this.render_rules());
		else panel.html(this.render_queue());
	}

	render_connect() {
		const c = this.data.connection || {};
		return `
			<div class="ng-po-panel">
				<h3>${__("Mailbox connection")}</h3>
				<p class="ng-po-panel-lead">${this.escape(c.message || __("Choose a provider. Mock fixtures work without secrets."))}</p>
				<div style="margin-bottom:12px">${this.status_chip(c.status)} <span class="ng-po-meta">${this.escape(c.provider || "")}</span></div>
				<form data-form="connect" class="ng-po-form-grid">
					<div class="ng-po-field">
						<label>${__("Provider")}</label>
						<select name="provider">
							${["Mock", "Gmail", "Microsoft", "IMAP"]
								.map(
									(p) =>
										`<option value="${p}" ${
											(c.provider_setting || c.provider) === p ? "selected" : ""
										}>${p}</option>`
								)
								.join("")}
						</select>
					</div>
					<div class="ng-po-field">
						<label>${__("Mailbox email")}</label>
						<input name="mailbox_email" value="${this.escape(c.mailbox_email || "")}" placeholder="purchase@company.com" />
					</div>
					<div class="ng-po-field">
						<label>${__("Watch folder / label")}</label>
						<input name="watch_folder" value="${this.escape(c.watch_folder || "INBOX/Purchase Orders")}" />
					</div>
					<div class="ng-po-field ng-po-check">
						<input type="checkbox" name="use_mock_fixtures" ${c.use_mock_fixtures ? "checked" : ""} />
						<span>${__("Use mock fixtures (recommended without OAuth secrets)")}</span>
					</div>
					<div class="ng-po-field">
						<label>${__("OAuth client ID")}</label>
						<input name="oauth_client_id" value="" placeholder="${c.has_oauth_client ? __("Saved — leave blank to keep") : ""}" />
					</div>
					<div class="ng-po-field">
						<label>${__("OAuth client secret")}</label>
						<input name="oauth_client_secret" type="password" value="" placeholder="${c.has_oauth_secret ? __("Saved — leave blank to keep") : ""}" />
					</div>
					<div class="ng-po-field">
						<label>${__("OAuth refresh token")}</label>
						<input name="oauth_refresh_token" type="password" value="" placeholder="${c.has_refresh_token ? __("Saved — leave blank to keep") : ""}" />
					</div>
					<div class="ng-po-field">
						<label>${__("Microsoft tenant ID")}</label>
						<input name="oauth_tenant_id" value="common" />
					</div>
					<div class="ng-po-field">
						<label>${__("IMAP host")}</label>
						<input name="imap_host" value="" />
					</div>
					<div class="ng-po-field">
						<label>${__("IMAP username")}</label>
						<input name="imap_username" value="" />
					</div>
					<div class="ng-po-field">
						<label>${__("IMAP password")}</label>
						<input name="imap_password" type="password" value="" placeholder="${c.has_imap_password ? __("Saved — leave blank to keep") : ""}" />
					</div>
					<div class="ng-po-field full">
						<div class="ng-po-actions">
							<button type="submit" class="ng-po-btn primary">${__("Save connection")}</button>
							${
								c.auth_url
									? `<a class="ng-po-btn" href="${this.escape(c.auth_url)}" target="_blank" rel="noopener">${__(
											"Open OAuth consent"
									  )}</a>`
									: ""
							}
							<button type="button" class="ng-po-btn" data-action="open-settings">${__("Open DocType settings")}</button>
						</div>
						<p class="ng-po-meta" style="margin-top:10px">${__("Last sync")}: ${this.escape(c.last_sync_at || "—")} — ${this.escape(
			c.last_sync_note || ""
		)}</p>
					</div>
				</form>
			</div>
		`;
	}

	render_rules() {
		const rules = this.data.rules || [];
		const rows = rules
			.map(
				(rule) => `
			<tr>
				<td>${cint(rule.enabled) ? "✓" : "—"}</td>
				<td><strong>${this.escape(rule.rule_name || rule.name)}</strong><div class="ng-po-meta">P${this.escape(
					rule.priority
				)} · ${this.escape(rule.rule_type)}</div></td>
				<td>${this.escape(rule.action)}</td>
				<td>${this.escape(rule.field_name || "—")}</td>
				<td>${this.escape((rule.value || "").toString().slice(0, 80))}</td>
				<td>
					<button type="button" class="ng-po-btn" data-action="edit-rule" data-name="${this.escape(rule.name || rule.rule_name)}">${__(
					"Edit"
				)}</button>
					<button type="button" class="ng-po-btn danger" data-action="delete-rule" data-name="${this.escape(
						rule.name || rule.rule_name
					)}">${__("Delete")}</button>
				</td>
			</tr>`
			)
			.join("");

		return `
			<div class="ng-po-panel">
				<h3>${__("Validation rules")}</h3>
				<p class="ng-po-panel-lead">${__("Mirror the Excel checks: vendor lists, amount thresholds, required fields, SKU allowlists.")}</p>
				<div class="ng-po-actions" style="margin-top:0;margin-bottom:12px">
					<button type="button" class="ng-po-btn primary" data-action="new-rule">${__("Add rule")}</button>
					<button type="button" class="ng-po-btn" data-action="open-rules-list">${__("Open DocType list")}</button>
				</div>
				${
					rules.length
						? `<table class="ng-po-table"><thead><tr>
							<th>${__("On")}</th><th>${__("Rule")}</th><th>${__("On fail")}</th><th>${__("Field")}</th><th>${__("Value")}</th><th></th>
						</tr></thead><tbody>${rows}</tbody></table>`
						: `<div class="ng-po-empty">${__("No rules yet — defaults are seeded on first sync.")}</div>`
				}
			</div>
		`;
	}

	render_queue() {
		const queue = this.data.queue || [];
		if (!queue.length) {
			return `
				<div class="ng-po-panel">
					<h3>${__("Review queue")}</h3>
					<p class="ng-po-panel-lead">${__("Sync the inbox to extract purchase-order drafts and run validation rules.")}</p>
					<div class="ng-po-empty">${__("Queue is empty. Click Sync Inbox to load mock purchase emails.")}</div>
					<div class="ng-po-actions"><button type="button" class="ng-po-btn primary" data-action="sync">${__("Sync Inbox")}</button></div>
				</div>
			`;
		}

		const cards = queue
			.map((row) => {
				const items = (row.items || [])
					.map(
						(item) =>
							`<li>${this.escape(item.sku || item.item_code || "—")} · ${this.escape(item.item_name || "")} · qty ${this.escape(
								item.qty
							)} · ${this.money(item.amount)}</li>`
					)
					.join("");
				return `
				<article class="ng-po-queue-card" data-intake="${this.escape(row.name)}">
					<div class="ng-po-queue-head">
						<div>
							<strong>${this.escape(row.subject || row.name)}</strong>
							<small>${this.escape(row.from_email || "")} · ${this.escape(row.received_at || "")}</small>
						</div>
						<span class="ng-po-badge ${this.badge_class(row.status)}">${this.escape(row.status)}</span>
					</div>
					<div class="ng-po-meta">
						${__("Vendor")}: <strong>${this.escape(row.vendor_name || "—")}</strong>
						· ${__("Ref")}: ${this.escape(row.po_reference || "—")}
						· ${__("Total")}: ${this.money(row.total_amount)}
						· ${__("Confidence")}: ${this.escape(row.confidence || 0)}
					</div>
					<div class="ng-po-meta">${__("Rules")}: ${this.escape(row.rule_decision || "—")} — ${this.escape(row.rule_summary || "")}</div>
					${items ? `<ul class="ng-po-items">${items}</ul>` : ""}
					<div class="ng-po-actions">
						<button type="button" class="ng-po-btn" data-action="open-intake" data-name="${this.escape(row.name)}">${__("Open")}</button>
						${
							["Needs Review", "Flagged", "Draft"].includes(row.status)
								? `<button type="button" class="ng-po-btn primary" data-action="approve" data-name="${this.escape(
										row.name
								  )}">${__("Approve")}</button>
							<button type="button" class="ng-po-btn danger" data-action="reject" data-name="${this.escape(row.name)}">${__(
										"Reject"
								  )}</button>`
								: ""
						}
						${
							["Approved", "Needs Review", "Flagged"].includes(row.status) && !row.purchase_order
								? `<button type="button" class="ng-po-btn" data-action="export" data-name="${this.escape(
										row.name
								  )}">${__("Export Draft PO")}</button>`
								: ""
						}
						${
							row.purchase_order
								? `<button type="button" class="ng-po-btn" data-action="open-po" data-name="${this.escape(
										row.purchase_order
								  )}">${__("PO")} ${this.escape(row.purchase_order)}</button>`
								: ""
						}
					</div>
				</article>`;
			})
			.join("");

		return `
			<div class="ng-po-panel">
				<h3>${__("Review queue")}</h3>
				<p class="ng-po-panel-lead">${__("Humans confirm every draft before a Purchase Order is created. Export always stays Draft.")}</p>
				${cards}
			</div>
		`;
	}

	form_values(form) {
		const data = {};
		$(form)
			.serializeArray()
			.forEach((row) => {
				data[row.name] = row.value;
			});
		const mock = form.querySelector('[name="use_mock_fixtures"]');
		if (mock) data.use_mock_fixtures = mock.checked ? 1 : 0;
		data.enabled = 1;
		["oauth_client_id", "oauth_client_secret", "oauth_refresh_token", "imap_host", "imap_username", "imap_password"].forEach(
			(key) => {
				if (!data[key]) delete data[key];
			}
		);
		return data;
	}

	async handle_form(event) {
		const form = event.currentTarget;
		const kind = $(form).data("form");
		if (kind === "connect") {
			try {
				await this.api("save_connection", { values: this.form_values(form) });
				frappe.show_alert({ message: __("Connection saved"), indicator: "green" });
				await this.refresh();
			} catch (error) {
				frappe.msgprint(error.message || __("Save failed"));
			}
		}
	}

	async handle_action(event) {
		const $btn = $(event.currentTarget);
		const action = $btn.data("action");
		const name = $btn.data("name");

		if (action === "sync") return this.sync_inbox();
		if (action === "open-settings") return frappe.set_route("Form", "NextGen PO Email Settings");
		if (action === "open-rules-list") return frappe.set_route("List", "NextGen PO Email Rule");
		if (action === "open-intake") return frappe.set_route("Form", "NextGen PO Email Intake", name);
		if (action === "open-po") return frappe.set_route("Form", "Purchase Order", name);
		if (action === "new-rule") return this.prompt_rule();
		if (action === "edit-rule") {
			const rule = (this.data.rules || []).find((row) => row.name === name || row.rule_name === name);
			return this.prompt_rule(rule);
		}
		if (action === "delete-rule") {
			frappe.confirm(__("Delete rule {0}?", [name]), async () => {
				await this.api("delete_rule", { name });
				await this.refresh();
			});
			return;
		}
		if (action === "approve" || action === "reject") {
			const decision = action === "approve" ? "Approved" : "Rejected";
			await this.api("decide_intake", { name, decision });
			frappe.show_alert({ message: __("{0} marked {1}", [name, decision]), indicator: "green" });
			return this.refresh();
		}
		if (action === "export") {
			try {
				const result = await this.api("export_to_purchase_order", { name });
				frappe.show_alert({
					message: __("Draft PO {0} created", [result.purchase_order]),
					indicator: "green",
				});
				await this.refresh();
			} catch (error) {
				frappe.msgprint(error.message || __("Export failed — resolve Supplier/Item links first"));
			}
		}
	}

	prompt_rule(existing) {
		const d = new frappe.ui.Dialog({
			title: existing ? __("Edit rule") : __("Add rule"),
			fields: [
				{ fieldname: "enabled", label: __("Enabled"), fieldtype: "Check", default: existing ? cint(existing.enabled) : 1 },
				{
					fieldname: "rule_name",
					label: __("Rule name"),
					fieldtype: "Data",
					reqd: 1,
					default: existing?.rule_name || "",
				},
				{ fieldname: "priority", label: __("Priority"), fieldtype: "Int", default: existing?.priority || 100 },
				{
					fieldname: "rule_type",
					label: __("Rule type"),
					fieldtype: "Select",
					options:
						"Vendor Allowlist\nVendor Blocklist\nAmount Max\nAmount Min\nRequired Field\nSKU Exists\nSKU Allowlist\nCustom Expression",
					default: existing?.rule_type || "Amount Max",
					reqd: 1,
				},
				{
					fieldname: "action",
					label: __("Action on fail"),
					fieldtype: "Select",
					options: "Approve\nReject\nFlag",
					default: existing?.action || "Flag",
					reqd: 1,
				},
				{ fieldname: "field_name", label: __("Field name"), fieldtype: "Data", default: existing?.field_name || "" },
				{
					fieldname: "compare_op",
					label: __("Compare"),
					fieldtype: "Select",
					options: "<=\n>=\n=\n!=",
					default: existing?.compare_op || "<=",
				},
				{ fieldname: "value", label: __("Value"), fieldtype: "Small Text", default: existing?.value || "" },
				{ fieldname: "message", label: __("Message"), fieldtype: "Small Text", default: existing?.message || "" },
			],
			primary_action_label: __("Save"),
			primary_action: async (values) => {
				if (existing?.name) values.name = existing.name;
				await this.api("save_rule", { rule: values });
				d.hide();
				await this.refresh();
			},
		});
		d.show();
	}
}
