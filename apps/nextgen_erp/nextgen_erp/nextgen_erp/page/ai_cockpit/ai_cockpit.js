frappe.pages["ai-cockpit"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: __("AI Cockpit"),
		single_column: true,
	});

	wrapper.ai_command_center = new NextGenAICommandCenter(wrapper);
};

frappe.pages["ai-cockpit"].on_page_show = function (wrapper) {
	wrapper.ai_command_center?.refresh();
};

class NextGenAICommandCenter {
	constructor(wrapper) {
		this.wrapper = wrapper;
		this.page = wrapper.page;
		this.days = 30;
		this.charts = [];
		this.render_shell();
		this.setup_actions();
	}

	render_shell() {
		this.$root = $(
			`<div class="ng-command-center">
				<section class="ng-command-hero">
					<div>
						<div class="ng-eyebrow">NEXTGEN INTELLIGENCE</div>
						<h2>AI Cockpit</h2>
						<p>${__("ภาพรวมงานขาย จัดซื้อ และความเสี่ยงสต๊อก จากข้อมูล ERP แบบเรียลไทม์")}</p>
					</div>
					<div class="ng-hero-agents">
						<button type="button" data-route="ai-sales-copilot" class="ng-agent-pill ng-agent-sales">${frappe.utils.icon("bot-message-square", "md")} Sales Copilot</button>
						<button type="button" data-route="ai-procurement-copilot" class="ng-agent-pill ng-agent-buying">${frappe.utils.icon("shopping-cart", "md")} Procurement Copilot</button>
					</div>
				</section>
				<div class="ng-command-meta">
					<label>${__("ช่วงเวลา")} <select class="ng-period-filter"><option value="7">${__("7 วัน")}</option><option value="30" selected>${__("30 วัน")}</option><option value="90">${__("90 วัน")}</option><option value="365">${__("12 เดือน")}</option></select></label>
					<span class="ng-live-status"><span class="ng-live-dot"></span><span class="ng-generated">${__("กำลังโหลดข้อมูล…")}</span></span>
				</div>
			<section class="ng-kpi-grid">
				<div class="ng-skeleton" aria-hidden="true"></div>
				<div class="ng-skeleton" aria-hidden="true"></div>
				<div class="ng-skeleton" aria-hidden="true"></div>
				<div class="ng-skeleton" aria-hidden="true"></div>
			</section>
			<section class="ng-chart-grid">
				<article class="ng-panel ng-panel-wide"><div class="ng-panel-head"><div><span>BUSINESS FLOW</span><h3>Sales vs Procurement</h3></div><small>${__("6 เดือนล่าสุด")}</small></div><div id="ng-flow-chart" class="ng-chart"><div class="ng-skeleton ng-skeleton-chart" aria-hidden="true"></div></div></article>
				<article class="ng-panel"><div class="ng-panel-head"><div><span>ORDER INTAKE</span><h3>${__("สถานะงานขาย")}</h3></div></div><div id="ng-intake-chart" class="ng-chart"><div class="ng-skeleton ng-skeleton-chart" aria-hidden="true"></div></div></article>
				<article class="ng-panel"><div class="ng-panel-head"><div><span>INVENTORY SIGNAL</span><h3>${__("ความเสี่ยงสต๊อก")}</h3></div></div><div id="ng-risk-chart" class="ng-chart"><div class="ng-skeleton ng-skeleton-chart" aria-hidden="true"></div></div></article>
			</section>
			<section class="ng-bottom-grid">
				<article class="ng-panel"><div class="ng-panel-head"><div><span>ACTION REQUIRED</span><h3>${__("งานที่ต้องดูแล")}</h3></div></div><div class="ng-attention-list"></div></article>
				<article class="ng-panel"><div class="ng-panel-head"><div><span>LIVE ACTIVITY</span><h3>${__("ความเคลื่อนไหวล่าสุด")}</h3></div></div><div class="ng-activity-list"></div></article>
				</section>
			</div>`
		).appendTo($(this.wrapper).find(".layout-main-section").empty());

		this.$root.on("click", "[data-route]", (event) => frappe.set_route($(event.currentTarget).data("route")));
		this.$root.on("click", "[data-document]", (event) => {
			const [doctype, name] = JSON.parse($(event.currentTarget).attr("data-document"));
			frappe.set_route("Form", doctype, name);
		});
		this.$root.on("click", "[data-list]", (event) => {
			frappe.set_route("List", $(event.currentTarget).data("list"));
		});
		this.$root.on("click", '[data-action="retry"]', () => this.refresh());
	}

	setup_actions() {
		this.$root.find(".ng-period-filter").on("change", (event) => {
			this.days = Number(event.target.value) || 30;
			this.refresh();
		});
		this.page.set_primary_action(__("Refresh"), () => this.refresh(), "refresh");
	}

	async refresh() {
		this.$root.addClass("is-loading");
		try {
			const data = await frappe.xcall("nextgen_erp.dashboard.get_command_center_data", { days: this.days });
			this.data = data;
			this.render(data);
		} catch (error) {
			this.render_error();
			throw error;
		} finally {
			this.$root.removeClass("is-loading");
		}
	}

	render_error() {
		this.$root.find(".ng-generated").text(__("โหลดข้อมูลไม่สำเร็จ"));
		this.$root.find(".ng-kpi-grid").html(`
			<div class="ng-error-panel" role="alert">
				<strong>${__("ไม่สามารถโหลด dashboard ได้")}</strong>
				<small>${__("ตรวจสอบการเชื่อมต่อ แล้วลองอีกครั้ง")}</small>
				<button type="button" class="ng-retry-btn" data-action="retry">${__("ลองใหม่")}</button>
			</div>`);
	}

	format_currency(value) {
		return new Intl.NumberFormat("th-TH", { style: "currency", currency: this.data.currency || "THB", maximumFractionDigits: 0 }).format(value || 0);
	}

	render(data) {
		const cards = [
			{ label: __("ยอดขายจาก Sales Order"), value: this.format_currency(data.kpis.sales_value), detail: __("{0} ออเดอร์", [data.kpis.sales_orders]), icon: "trending-up", tone: "blue", list: "Sales Order" },
			{ label: __("มูลค่าจัดซื้อ"), value: this.format_currency(data.kpis.purchase_value), detail: __("{0} ใบสั่งซื้อ", [data.kpis.purchase_orders]), icon: "shopping-cart", tone: "green", list: "Purchase Order" },
			{ label: __("ต้องดำเนินการ"), value: data.kpis.attention.toLocaleString("th-TH"), detail: __("รวมทุก Copilot"), icon: "circle-alert", tone: "amber", route: "ai-cockpit" },
			{ label: __("Automation Success"), value: `${data.kpis.automation_rate}%`, detail: __("Order Intake อัตโนมัติ"), icon: "sparkles", tone: "violet", route: "ai-sales-copilot" },
		];
		this.$root.find(".ng-kpi-grid").html(cards.map((card) => `
			<button type="button" class="ng-kpi ng-${card.tone}" ${card.list ? `data-list="${card.list}"` : `data-route="${card.route}"`}>
				<span class="ng-kpi-icon">${frappe.utils.icon(card.icon, "lg")}</span>
				<span class="ng-kpi-copy"><small>${card.label}</small><strong>${card.value}</strong><em>${card.detail}</em></span>
				<span class="ng-kpi-arrow">${frappe.utils.icon("arrow-up-right", "sm")}</span>
			</button>`).join(""));

		this.$root.find(".ng-generated").text(__("ข้อมูลสด · {0} · {1} วันล่าสุด", [data.company || __("ทุกบริษัท"), data.period_days]));
		this.render_charts(data);
		this.render_attention(data.attention);
		this.render_activities(data.activities);
	}

	render_charts(data) {
		// Destroy previous chart instances before re-creating; frappe-charts
		// attaches listeners that otherwise leak on every period switch.
		this.charts.forEach((chart) => chart?.destroy?.());
		this.charts = [];
		["#ng-flow-chart", "#ng-intake-chart", "#ng-risk-chart"].forEach((selector) => this.$root.find(selector).empty());
		const total_sales = (data.trend.sales || []).reduce((sum, value) => sum + value, 0);
		const total_purchases = (data.trend.purchases || []).reduce((sum, value) => sum + value, 0);
		this.$root.find("#ng-flow-chart").attr({
			role: "img",
			"aria-label": __("กราฟเส้น 6 เดือนล่าสุด ยอดขายรวม {0} จัดซื้อรวม {1}", [this.format_currency(total_sales), this.format_currency(total_purchases)]),
		});
		this.charts.push(new frappe.Chart("#ng-flow-chart", {
			type: "line", height: 250, colors: ["#3b82f6", "#10b981"],
			data: { labels: data.trend.labels, datasets: [{ name: "Sales", values: data.trend.sales }, { name: "Procurement", values: data.trend.purchases }] },
			axisOptions: { xAxisMode: "tick", yAxisMode: "tick", xIsSeries: true },
			lineOptions: { regionFill: 1, hideDots: 0, spline: 1 },
			tooltipOptions: { formatTooltipY: (value) => this.format_currency(value) },
		}));
		this.charts.push(this.make_donut("#ng-intake-chart", data.intake_status, ["#f59e0b", "#3b82f6", "#8b5cf6", "#10b981", "#cbd5e1"], __("สถานะงานขาย")));
		this.charts.push(this.make_donut("#ng-risk-chart", data.stock_risk, ["#ef4444", "#f59e0b", "#10b981"], __("ความเสี่ยงสต๊อก")));
	}

	make_donut(selector, values, colors, title) {
		const has_data = values.values.some((value) => value > 0);
		if (!has_data) {
			this.$root.find(selector).html(`<div class="ng-empty-chart">${__("ยังไม่มีข้อมูลในช่วงเวลานี้")}</div>`);
			return null;
		}
		const summary = values.labels.map((label, index) => `${label}: ${values.values[index]}`).join(", ");
		this.$root.find(selector).attr({ role: "img", "aria-label": `${title} — ${summary}` });
		return new frappe.Chart(selector, { type: "donut", height: 230, colors, data: { labels: values.labels, datasets: [{ values: values.values }] } });
	}

	render_attention(attention) {
		const rows = [
			{ label: "Sales Order Intake", detail: __("รอตรวจสอบหรือรอลูกค้า"), value: attention.sales, icon: "bot-message-square", tone: "blue", list: "AI Order Intake" },
			{ label: "Procurement Recommendations", detail: __("คำแนะนำที่ยังไม่จบงาน"), value: attention.procurement, icon: "shopping-cart", tone: "green", list: "NextGen Procurement Recommendation" },
			{ label: "Stockout Risk", detail: __("สินค้าเสี่ยงขาดระดับสูง"), value: attention.stock, icon: "triangle-alert", tone: "red", list: "NextGen Procurement Forecast" },
		];
		this.$root.find(".ng-attention-list").html(rows.map((row) => `
			<button type="button" class="ng-attention-row" data-list="${row.list}"><span class="ng-mini-icon ng-${row.tone}">${frappe.utils.icon(row.icon, "md")}</span><span><strong>${row.label}</strong><small>${row.detail}</small></span><b>${row.value}</b>${frappe.utils.icon("chevron-right", "sm")}</button>`).join(""));
	}

	render_activities(activities) {
		const escape = frappe.utils.escape_html;
		if (!activities.length) {
			this.$root.find(".ng-activity-list").html(`<div class="ng-empty-chart">${__("ยังไม่มีกิจกรรมล่าสุด")}</div>`);
			return;
		}
		this.$root.find(".ng-activity-list").html(activities.map((row) => {
			const doc = JSON.stringify([row.doctype, row.name]).replace(/'/g, "&#39;");
			return `<button type="button" class="ng-activity-row" data-document='${doc}'><span class="ng-activity-dot ng-${row.tone}"></span><span><small>${escape(row.kind)}</small><strong>${escape(row.title)}</strong><em>${escape(row.detail || "")}</em></span><time>${frappe.datetime.prettyDate(row.modified)}</time></button>`;
		}).join(""));
	}
}
