/* global frappe */

(() => {
	"use strict";

	const METHOD = "nextgen_erp.staff_chat.";
	const SESSION_KEY_PREFIX = "nextgen_staff_chat_session:";
	const ACTIVE_AGENT_KEY = "nextgen_ai_active_agent";
	const BRAND_ICON = "/assets/nextgen_erp/images/nextgen-icon.svg";
	const state = {
		open: false,
		brand: "NextGen AI",
		agents: [],
		agentKey: null,
		sessionId: null,
		turnId: null,
		ignoredTurn: null,
		messages: [],
		actions: new Map(),
		lastInput: "",
	};

	function escapeHtml(value) {
		return String(value ?? "")
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/\"/g, "&quot;")
			.replace(/'/g, "&#039;");
	}

	function renderText(value) {
		return escapeHtml(value)
			.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
			.replace(/`(.+?)`/g, "<code>$1</code>")
			.replace(/\n/g, "<br>");
	}

	function call(method, args = {}) {
		return frappe.call({ method: METHOD + method, args }).then((response) => response.message);
	}

	function num(value, digits = 0) {
		return Number(value || 0).toLocaleString("th-TH", {
			minimumFractionDigits: digits,
			maximumFractionDigits: Math.max(digits, 2),
		});
	}

	function pageContext() {
		const route = frappe.get_route?.() || [];
		const context = { route: route.join("/") };
		if (route[0] === "Form" && route[1]) {
			context.doctype = route[1];
			if (route[2]) context.name = route[2];
		} else if (route[0] === "List" && route[1]) {
			context.doctype = route[1];
		}
		return context;
	}

	function makeTurnId() {
		if (window.crypto?.randomUUID) return window.crypto.randomUUID();
		return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (character) => {
			const random = Math.floor(Math.random() * 16);
			const value = character === "x" ? random : (random & 3) | 8;
			return value.toString(16);
		});
	}

	// ------------------------------------------------------------------
	// Agents
	// ------------------------------------------------------------------

	function agentByKey(key) {
		return state.agents.find((agent) => agent.key === key) || null;
	}

	function activeAgent() {
		return agentByKey(state.agentKey);
	}

	function matchRouteAgent() {
		const route = (frappe.get_route?.() || []).join("/").toLowerCase().replace(/\s+/g, "-");
		if (!route) return null;
		for (const agent of state.agents) {
			if ((agent.route_keywords || []).some((keyword) => route.includes(keyword))) {
				return agent.key;
			}
		}
		return null;
	}

	function sessionStorageKey(agentKey) {
		return SESSION_KEY_PREFIX + agentKey;
	}

	function welcomeMessage(agent) {
		return {
			role: "assistant",
			content: agent.welcome_message,
			suggestions: (agent.suggested_questions || []).slice(0, 6),
		};
	}

	function agentChooserMessage() {
		return {
			role: "assistant",
			content: "ต้องการใช้ผู้ช่วยด้านไหนคะ? เลือกได้จากตัวเลือกด้านล่าง หรือเปลี่ยนภายหลังได้จากเมนูด้านบน",
			agentChoices: state.agents.map((agent) => agent.key),
		};
	}

	function applyAgentTheme(agent) {
		const panel = document.getElementById("nextgen-chat-panel");
		if (panel) panel.style.setProperty("--ng-agent-color", agent?.color || "#2563eb");
		const brandIcon = document.getElementById("nextgen-agent-icon");
		if (brandIcon && agent) brandIcon.src = agent.icon;
		const brandTitle = document.getElementById("nextgen-agent-title");
		if (brandTitle && agent) brandTitle.textContent = agent.title;
		const brandSub = document.getElementById("nextgen-agent-subtitle");
		if (brandSub && agent) brandSub.textContent = `${state.brand} · ${agent.subtitle || "Typhoon AI"}`;
		const menu = document.getElementById("nextgen-agent-menu");
		if (menu) {
			menu.querySelectorAll("[data-agent]").forEach((button) => {
				button.classList.toggle("is-active", button.dataset.agent === state.agentKey);
			});
		}
		const input = document.getElementById("nextgen-chat-input");
		if (input && agent) {
			input.placeholder =
				agent.key === "procurement"
					? "ถามเรื่องสต๊อก demand ความเสี่ยงของขาด หรือเตรียม PO..."
					: "ถามข้อมูลสินค้า สต๊อก ราคา หรือสร้างออเดอร์...";
		}
	}

	function selectAgent(agentKey, { restore = true } = {}) {
		const agent = agentByKey(agentKey);
		if (!agent || state.turnId) return;
		state.agentKey = agentKey;
		window.localStorage.setItem(ACTIVE_AGENT_KEY, agentKey);
		state.sessionId = window.localStorage.getItem(sessionStorageKey(agentKey));
		state.turnId = null;
		state.actions.clear();
		state.messages = [welcomeMessage(agent)];
		applyAgentTheme(agent);
		render();
		if (restore && state.sessionId) restoreSession(state.sessionId, false);
	}

	// ------------------------------------------------------------------
	// Mount + panel lifecycle
	// ------------------------------------------------------------------

	function mount() {
		if (document.getElementById("nextgen-chat-launcher")) return;
		const agentMenu = state.agents
			.map(
				(agent) => `<button type="button" data-agent="${escapeHtml(agent.key)}">
					<img src="${escapeHtml(agent.icon)}" alt="">
					<span><strong>${escapeHtml(agent.title)}</strong><small>${escapeHtml(agent.subtitle || "")}</small></span>
				</button>`,
			)
			.join("");
		document.body.insertAdjacentHTML(
			"beforeend",
			`<button id="nextgen-chat-launcher" type="button" aria-label="เปิด ${escapeHtml(state.brand)}" title="${escapeHtml(state.brand)}">
				<img src="${BRAND_ICON}" alt="" aria-hidden="true">
				<span class="ng-launcher-label">${escapeHtml(state.brand)}</span>
			</button>
			<aside id="nextgen-chat-panel" aria-label="${escapeHtml(state.brand)}" aria-hidden="true">
				<header class="ng-chat-header">
					<div class="ng-chat-brand">
						<img id="nextgen-agent-icon" src="${BRAND_ICON}" alt="">
						<div>
							<button id="nextgen-agent-switcher" type="button" aria-haspopup="true" aria-expanded="false" title="เปลี่ยนผู้ช่วย">
								<strong id="nextgen-agent-title">${escapeHtml(state.brand)}</strong>
								<span class="ng-caret" aria-hidden="true">▾</span>
							</button>
							<small id="nextgen-agent-subtitle">${escapeHtml(state.brand)} · Typhoon AI</small>
						</div>
					</div>
					<div class="ng-chat-header-actions">
						<button data-action="history" title="ประวัติ">☰</button>
						<button data-action="new" title="แชตใหม่">＋</button>
						<button data-action="close" title="ปิด">×</button>
					</div>
				</header>
				<div id="nextgen-agent-menu" hidden>${agentMenu}</div>
				<div id="nextgen-chat-history" hidden></div>
				<div id="nextgen-chat-messages" role="log" aria-live="polite"></div>
				<div id="nextgen-chat-status"></div>
				<footer class="ng-chat-composer">
					<textarea id="nextgen-chat-input" rows="2" maxlength="4000" placeholder="พิมพ์คำถาม..."></textarea>
					<div>
						<button id="nextgen-chat-stop" type="button" hidden>หยุด</button>
						<button id="nextgen-chat-send" type="button">ส่ง</button>
					</div>
				</footer>
			</aside>`,
		);

		document.getElementById("nextgen-chat-launcher").addEventListener("click", toggle);
		document.querySelector('[data-action="close"]').addEventListener("click", close);
		document.querySelector('[data-action="new"]').addEventListener("click", newChat);
		document.querySelector('[data-action="history"]').addEventListener("click", toggleHistory);
		document.getElementById("nextgen-chat-send").addEventListener("click", () => send());
		document.getElementById("nextgen-chat-stop").addEventListener("click", stopStreaming);
		document.getElementById("nextgen-agent-switcher").addEventListener("click", toggleAgentMenu);
		document.getElementById("nextgen-agent-menu").addEventListener("click", handleAgentMenuClick);
		document.getElementById("nextgen-chat-input").addEventListener("keydown", (event) => {
			if (event.key === "Enter" && !event.shiftKey) {
				event.preventDefault();
				send();
			} else if (event.key === "Escape") {
				close();
			}
		});
		document.getElementById("nextgen-chat-messages").addEventListener("click", handleMessageClick);
		document.getElementById("nextgen-chat-history").addEventListener("click", handleHistoryClick);

		// Pre-select the stored agent so the header is never anonymous.
		const stored = window.localStorage.getItem(ACTIVE_AGENT_KEY);
		if (stored && agentByKey(stored)) {
			selectAgent(stored);
		} else if (state.agents.length === 1) {
			selectAgent(state.agents[0].key);
		} else {
			state.messages = [agentChooserMessage()];
			render();
		}
	}

	function toggle() {
		state.open ? close() : open();
	}

	function open() {
		state.open = true;
		// Opening from a Sales/Buying route always selects the matching agent.
		const routeAgent = matchRouteAgent();
		if (routeAgent && routeAgent !== state.agentKey && !state.turnId) {
			selectAgent(routeAgent);
		} else if (!state.agentKey) {
			if (state.agents.length === 1) selectAgent(state.agents[0].key);
			else {
				state.messages = [agentChooserMessage()];
				render();
			}
		}
		const panel = document.getElementById("nextgen-chat-panel");
		panel.classList.add("is-open");
		panel.setAttribute("aria-hidden", "false");
		document.getElementById("nextgen-chat-input").focus();
	}

	function close() {
		state.open = false;
		const panel = document.getElementById("nextgen-chat-panel");
		panel?.classList.remove("is-open");
		panel?.setAttribute("aria-hidden", "true");
		const menu = document.getElementById("nextgen-agent-menu");
		if (menu) menu.hidden = true;
	}

	function toggleAgentMenu() {
		const menu = document.getElementById("nextgen-agent-menu");
		const switcher = document.getElementById("nextgen-agent-switcher");
		menu.hidden = !menu.hidden;
		switcher.setAttribute("aria-expanded", String(!menu.hidden));
	}

	function handleAgentMenuClick(event) {
		const button = event.target.closest("[data-agent]");
		if (!button) return;
		document.getElementById("nextgen-agent-menu").hidden = true;
		if (button.dataset.agent !== state.agentKey) selectAgent(button.dataset.agent);
	}

	function newChat() {
		if (state.turnId) return;
		const agent = activeAgent();
		state.sessionId = null;
		state.actions.clear();
		if (state.agentKey) window.localStorage.removeItem(sessionStorageKey(state.agentKey));
		state.messages = agent ? [welcomeMessage(agent)] : [agentChooserMessage()];
		document.getElementById("nextgen-chat-history").hidden = true;
		render();
	}

	// ------------------------------------------------------------------
	// Turns
	// ------------------------------------------------------------------

	async function send(text) {
		const input = document.getElementById("nextgen-chat-input");
		const value = String(text ?? input.value).trim();
		if (!value || state.turnId) return;
		if (!state.agentKey) {
			state.messages.push(agentChooserMessage());
			render();
			return;
		}
		state.lastInput = value;
		input.value = "";
		state.messages.push({ role: "user", content: value });
		const pending = { role: "assistant", content: "", pending: true, action: null, forecasts: [] };
		state.messages.push(pending);
		state.turnId = makeTurnId();
		pending.turnId = state.turnId;
		setBusy(true, "Typhoon กำลังตรวจข้อมูล ERP...");
		render();
		try {
			const result = await call("start_turn", {
				session_id: state.sessionId,
				message: value,
				page_context: JSON.stringify(pageContext()),
				client_turn_id: state.turnId,
				agent_type: state.agentKey,
			});
			state.turnId = result.turn_id;
			state.sessionId = result.session_id;
			window.localStorage.setItem(sessionStorageKey(state.agentKey), state.sessionId);
			reconcileTurn(result.turn_id, result.session_id);
		} catch (error) {
			pending.pending = false;
			pending.error = true;
			pending.content = error?.message || "ไม่สามารถเริ่มแชตได้";
			state.turnId = null;
			setBusy(false);
			render();
		}
	}

	function reconcileTurn(turnId, sessionId, attempt = 0) {
		window.setTimeout(async () => {
			if (state.turnId !== turnId || state.ignoredTurn === turnId) return;
			try {
				const data = await call("get_session", { session_id: sessionId });
				const saved = (data.messages || []).find(
					(message) => message.turn_id === turnId && message.role === "assistant",
				);
				if (saved) {
					const pending = [...state.messages].reverse().find(
						(message) => message.pending && message.turnId === turnId,
					);
					if (!pending) return;
					pending.content = saved.content || pending.content;
					pending.pending = false;
					pending.error = saved.message_type === "error";
					pending.forecasts = saved.forecasts || pending.forecasts || [];
					if (saved.action) {
						const action = (data.actions || []).find((row) => row.name === saved.action);
						if (action) {
							pending.action = { ...action, action_id: action.name };
							state.actions.set(action.name, pending.action);
						}
					}
					state.turnId = null;
					setBusy(false);
					render();
					return;
				}
			} catch {
				// A later attempt can recover from a brief HTTP/realtime reconnect.
			}
			if (attempt < 23) reconcileTurn(turnId, sessionId, attempt + 1);
			else {
				const pending = [...state.messages].reverse().find(
					(message) => message.pending && message.turnId === turnId,
				);
				if (pending) {
					pending.pending = false;
					pending.error = true;
					pending.content ||= "คำตอบใช้เวลานานกว่าปกติ กรุณาลองอีกครั้งค่ะ";
				}
				state.turnId = null;
				setBusy(false);
				render();
			}
		}, 2500);
	}

	function stopStreaming() {
		state.ignoredTurn = state.turnId;
		state.turnId = null;
		const pending = [...state.messages].reverse().find((message) => message.pending);
		if (pending) {
			pending.pending = false;
			pending.content ||= "หยุดแสดงคำตอบแล้ว งานฝั่งเซิร์ฟเวอร์จะสิ้นสุดอย่างปลอดภัยค่ะ";
		}
		setBusy(false);
		render();
	}

	function onRealtime(event) {
		if (!event?.turn_id || event.turn_id === state.ignoredTurn) return;
		if (event.turn_id !== state.turnId) return;
		const pending = [...state.messages].reverse().find((message) => message.pending);
		if (!pending) return;
		if (event.type === "delta") {
			pending.content += event.text || "";
			render();
		} else if (event.type === "tool") {
			setStatus(`กำลังใช้เครื่องมือ: ${event.name}`);
		} else if (event.type === "action") {
			pending.action = event.action;
			state.actions.set(event.action.action_id, event.action);
			render();
		} else if (event.type === "forecast") {
			pending.forecasts = [...(pending.forecasts || []), event.forecast];
			render();
		} else if (event.type === "done") {
			pending.pending = false;
			state.turnId = null;
			setBusy(false);
			render();
		} else if (event.type === "error") {
			pending.pending = false;
			pending.error = true;
			pending.content ||= event.message || "ผู้ช่วยไม่พร้อมใช้งาน";
			state.turnId = null;
			setBusy(false);
			render();
		}
	}

	function setBusy(busy, text = "") {
		document.getElementById("nextgen-chat-send").disabled = busy;
		document.getElementById("nextgen-chat-input").disabled = busy;
		document.getElementById("nextgen-chat-stop").hidden = !busy;
		setStatus(text);
	}

	function setStatus(text) {
		const el = document.getElementById("nextgen-chat-status");
		if (el) el.textContent = text || "";
	}

	// ------------------------------------------------------------------
	// Cards
	// ------------------------------------------------------------------

	function riskBadge(risk) {
		const labels = { high: "เสี่ยงสูง", medium: "เสี่ยงปานกลาง", low: "เสี่ยงต่ำ" };
		return `<span class="ng-risk ng-risk-${escapeHtml(risk || "low")}">${labels[risk] || escapeHtml(risk || "-")}</span>`;
	}

	function forecastCard(forecast) {
		if (!forecast) return "";
		const warnings = (forecast.warnings || [])
			.map((warning) => `<li>${escapeHtml(warning)}</li>`)
			.join("");
		const rows = [
			["สต๊อกปัจจุบัน", `${num(forecast.actual_qty)} ${escapeHtml(forecast.stock_uom || "")}`],
			["จองแล้ว", num(forecast.reserved_qty)],
			["กำลังมา (PO)", num(forecast.incoming_qty)],
			["Demand 30/60/90 วัน", `${num(forecast.demand_30)} / ${num(forecast.demand_60)} / ${num(forecast.demand_90)}`],
			["เฉลี่ยต่อวัน", num(forecast.average_daily_demand, 2)],
			["Lead time", `${num(forecast.lead_time_days)} วัน`],
			["Days of supply", forecast.days_of_supply == null ? "-" : `${num(forecast.days_of_supply, 1)} วัน`],
			["Reorder point", num(forecast.reorder_point, 1)],
			["จำนวนแนะนำ", `<strong>${num(forecast.suggested_qty)} ${escapeHtml(forecast.stock_uom || "")}</strong>`],
			["คาดว่าจะขาด", forecast.stockout_date ? escapeHtml(forecast.stockout_date) : "-"],
		]
			.map(([label, value]) => `<div class="ng-forecast-metric"><small>${label}</small><span>${value}</span></div>`)
			.join("");
		const itemCode = escapeHtml(forecast.item_code || "");
		const qty = num(forecast.suggested_qty);
		const uom = escapeHtml(forecast.stock_uom || "");
		return `<section class="ng-forecast-card">
			<div class="ng-forecast-title">
				<strong>Forecast: ${escapeHtml(forecast.item_name || forecast.item_code)}</strong>
				${riskBadge(forecast.stockout_risk)}
			</div>
			<div class="ng-forecast-sub">${escapeHtml(forecast.item_code)} · Horizon ${num(forecast.horizon_days)} วัน · Data quality ${Math.round(Number(forecast.data_quality_score || 0) * 100)}%</div>
			<div class="ng-forecast-grid">${rows}</div>
			${warnings ? `<ul class="ng-action-warnings">${warnings}</ul>` : ""}
			<div class="ng-forecast-footer">${escapeHtml(forecast.formula_version || "")}</div>
			<div class="ng-message-actions">
				<button data-generate-forecast="${itemCode}">บันทึก Forecast</button>
				<button data-chat-prompt="สร้าง Purchase Order Preview สำหรับ ${itemCode} จำนวน ${qty} ${uom} จาก Forecast ล่าสุด">สร้าง PO Preview</button>
				<button data-chat-prompt="สร้าง Material Request Preview สำหรับ ${itemCode} จำนวน ${qty} ${uom} จาก Forecast ล่าสุด">สร้าง MR Preview</button>
			</div>
		</section>`;
	}

	function contextualActions(message, index) {
		if (
			message.role !== "assistant"
			|| message.pending
			|| message.error
			|| message.action
			|| (message.forecasts || []).length
			|| index === 0
		) return "";
		if (state.agentKey === "procurement") {
			return `<div class="ng-message-actions ng-inline-actions">
				<button data-generate-forecast="">Generate Forecast</button>
				<button data-chat-prompt="สร้าง Purchase Order Preview จากคำแนะนำล่าสุด โดยใช้ข้อมูล ERP ปัจจุบัน">สร้าง PO Preview</button>
				<button data-chat-prompt="สร้าง Material Request Preview จากคำแนะนำล่าสุด โดยใช้ข้อมูล ERP ปัจจุบัน">สร้าง MR Preview</button>
			</div>`;
		}
		return `<div class="ng-message-actions ng-inline-actions">
			<button data-start-sales-order="1">เริ่มสร้างออเดอร์</button>
		</div>`;
	}

	function procurementActionCard(action) {
		const preview = action.preview || {};
		const isPO = (action.action_type || preview.document_type) !== "prepare_material_request"
			&& preview.document_type !== "Material Request";
		const title = isPO ? "Purchase Order Preview" : "Material Request Preview";
		const items = (preview.items || [])
			.map((item) => {
				const variance = Number(item.price_variance_percent || 0);
				const varianceLabel = variance
					? `<small class="${variance > 0 ? "ng-var-up" : "ng-var-down"}">${variance > 0 ? "+" : ""}${variance.toFixed(2)}%</small>`
					: "";
				const constraint = [
					Number(item.min_order_qty) ? `MOQ ${num(item.min_order_qty)}` : "",
					Number(item.order_multiple) ? `x${num(item.order_multiple)}` : "",
				].filter(Boolean).join(" · ");
				return `<tr>
					<td>${escapeHtml(item.item_code)}<small>${escapeHtml(item.item_name || "")}${constraint ? ` · ${constraint}` : ""}</small></td>
					<td>${num(item.qty)} ${escapeHtml(item.uom || "")}<small>แนะนำ ${num(item.suggested_qty)}</small></td>
					<td>${num(item.rate, 2)}<small>ล่าสุด ${num(item.last_purchase_rate, 2)} ${varianceLabel}</small></td>
					<td>${num(item.amount, 2)}</td>
				</tr>`;
			})
			.join("");
		const warnings = (preview.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
		const status = action.status || "Pending";
		const result = action.result || {};
		const resultType = action.result_doctype || result.document_type;
		const resultName = action.result_name || result.document_name;
		return `<section class="ng-action-card ng-procurement-card" data-action-id="${escapeHtml(action.action_id)}">
			<div class="ng-action-title"><strong>${title}</strong><span>Data quality ${Math.round(Number(preview.data_quality_score ?? action.confidence ?? 0) * 100)}%</span></div>
			<div class="ng-action-customer">
				${isPO ? `Supplier: <strong>${escapeHtml(preview.supplier_name || preview.supplier || "-")}</strong><br>` : ""}
				บริษัท: ${escapeHtml(preview.company || "-")} · คลัง: ${escapeHtml(preview.warehouse || "-")}<br>
				กำหนดรับของ: ${escapeHtml(preview.schedule_date || "-")} · โหมด: ${escapeHtml(preview.automation_mode || "-")}
			</div>
			<table><tbody>${items}</tbody></table>
			<div class="ng-action-total">รวม ${num(preview.total, 2)} THB</div>
			${warnings ? `<ul class="ng-action-warnings">${warnings}</ul>` : ""}
			${preview.forecast_explanation ? `<div class="ng-forecast-footer">${escapeHtml(preview.forecast_explanation)}</div>` : ""}
			<div class="ng-action-buttons" ${status !== "Pending" ? "hidden" : ""}>
				<button data-confirm-action="${escapeHtml(action.action_id)}">ยืนยันและสร้าง</button>
				<button class="secondary" data-edit-action="${escapeHtml(action.action_id)}">แก้ข้อมูล</button>
				<button class="secondary" data-cancel-action="${escapeHtml(action.action_id)}">ยกเลิก</button>
			</div>
			<div class="ng-action-result">${status !== "Pending" ? escapeHtml(status) : ""}${resultType && resultName ? ` · <button class="ng-doc-link" data-document-type="${escapeHtml(resultType)}" data-document-name="${escapeHtml(resultName)}">${escapeHtml(resultName)}</button>` : ""}</div>
		</section>`;
	}

	function salesActionCard(action) {
		const preview = action.preview;
		const items = (preview.items || [])
			.map(
				(item) => `<tr><td>${escapeHtml(item.item_code)}</td><td>${escapeHtml(item.qty)} ${escapeHtml(item.uom)}</td><td>${num(item.amount)}</td></tr>`,
			)
			.join("");
		const warnings = (preview.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
		const status = action.status || "Pending";
		const result = action.result || {};
		const resultType = action.result_doctype || result.document_type;
		const resultName = action.result_name || result.document_name;
		return `<section class="ng-action-card" data-action-id="${escapeHtml(action.action_id)}">
			<div class="ng-action-title"><strong>Sales Order Preview</strong><span>${Math.round(Number(preview.confidence || 0) * 100)}%</span></div>
			<div class="ng-action-customer">ลูกค้า: <strong>${escapeHtml(preview.customer_name || preview.customer)}</strong></div>
			<table><tbody>${items}</tbody></table>
			<div class="ng-action-total">รวม ${num(preview.total, 2)} THB</div>
			${warnings ? `<ul class="ng-action-warnings">${warnings}</ul>` : ""}
			<div class="ng-action-buttons" ${status !== "Pending" ? "hidden" : ""}>
				<button data-confirm-action="${escapeHtml(action.action_id)}">ยืนยันและดำเนินการ</button>
				<button class="secondary" data-cancel-action="${escapeHtml(action.action_id)}">ยกเลิก</button>
			</div>
			<div class="ng-action-result">${status !== "Pending" ? escapeHtml(status) : ""}${resultType && resultName ? ` · <button class="ng-doc-link" data-document-type="${escapeHtml(resultType)}" data-document-name="${escapeHtml(resultName)}">${escapeHtml(resultName)}</button>` : ""}</div>
		</section>`;
	}

	function actionCard(action) {
		if (!action?.preview) return "";
		const type = action.action_type || "prepare_sales_order";
		if (type === "prepare_purchase_order" || type === "prepare_material_request") {
			return procurementActionCard(action);
		}
		return salesActionCard(action);
	}

	function render() {
		const container = document.getElementById("nextgen-chat-messages");
		if (!container) return;
		container.innerHTML = state.messages
			.map((message, index) => {
				const suggestions = (message.suggestions || [])
					.map((value) => `<button data-suggestion="${escapeHtml(value)}">${escapeHtml(value)}</button>`)
					.join("");
				const agentChoices = (message.agentChoices || [])
					.map((key) => {
						const agent = agentByKey(key);
						if (!agent) return "";
						return `<button class="ng-agent-choice" data-choose-agent="${escapeHtml(key)}">
							<img src="${escapeHtml(agent.icon)}" alt=""> ${escapeHtml(agent.title)}
						</button>`;
					})
					.join("");
				const forecasts = (message.forecasts || []).map(forecastCard).join("");
				const documentLink = message.href ? ` <button class="ng-doc-link" data-document-type="${escapeHtml(message.documentType)}" data-document-name="${escapeHtml(message.documentName)}">เปิดเอกสาร</button>` : "";
				const contextual = contextualActions(message, index);
				return `<div class="ng-message ng-${message.role} ${message.error ? "ng-error" : ""}" data-index="${index}">
					<div class="ng-bubble">${message.pending && !message.content ? '<span class="ng-thinking">กำลังคิด</span>' : renderText(message.content)}${documentLink}</div>
					${forecasts}
					${message.action ? actionCard(message.action) : ""}
					${contextual}
					${suggestions ? `<div class="ng-suggestions">${suggestions}</div>` : ""}
					${agentChoices ? `<div class="ng-suggestions ng-agent-choices">${agentChoices}</div>` : ""}
					${message.error ? '<button data-retry="1" class="ng-retry">ลองอีกครั้ง</button>' : ""}
				</div>`;
			})
			.join("");
		container.scrollTop = container.scrollHeight;
	}

	async function handleMessageClick(event) {
		const choose = event.target.closest("[data-choose-agent]");
		if (choose) return selectAgent(choose.dataset.chooseAgent);
		const suggestion = event.target.closest("[data-suggestion]");
		if (suggestion) return send(suggestion.dataset.suggestion);
		const prompt = event.target.closest("[data-chat-prompt]");
		if (prompt) return send(prompt.dataset.chatPrompt);
		const generate = event.target.closest("[data-generate-forecast]");
		if (generate) return openForecastDialog(generate.dataset.generateForecast);
		if (event.target.closest("[data-start-sales-order]")) return openSalesOrderDialog();
		if (event.target.closest("[data-retry]")) return send(state.lastInput);
		const confirm = event.target.closest("[data-confirm-action]");
		if (confirm) return confirmAction(confirm.dataset.confirmAction);
		const edit = event.target.closest("[data-edit-action]");
		if (edit) return editAction(edit.dataset.editAction);
		const cancel = event.target.closest("[data-cancel-action]");
		if (cancel) return cancelAction(cancel.dataset.cancelAction);
		const link = event.target.closest("[data-document-type]");
		if (link) frappe.set_route("Form", link.dataset.documentType, link.dataset.documentName);
	}

	function openSalesOrderDialog() {
		if (!state.sessionId || state.agentKey !== "sales") {
			frappe.msgprint("กรุณาเปิด AI Sales Copilot และเริ่มแชตก่อนค่ะ");
			return;
		}
		const dialog = new frappe.ui.Dialog({
			title: "สร้าง Sales Order Preview",
			fields: [
				{
					fieldname: "customer",
					fieldtype: "Link",
					options: "Customer",
					label: "ลูกค้า",
					reqd: 1,
				},
				{
					fieldname: "delivery_date",
					fieldtype: "Date",
					label: "วันที่ต้องการส่ง",
				},
				{
					fieldname: "items",
					fieldtype: "Table",
					label: "สินค้า",
					reqd: 1,
					in_place_edit: true,
					cannot_add_rows: false,
					data: [],
					fields: [
						{
							fieldname: "item",
							fieldtype: "Link",
							options: "Item",
							label: "สินค้า",
							in_list_view: 1,
							reqd: 1,
							get_query: () => ({ filters: { disabled: 0, is_sales_item: 1 } }),
						},
						{
							fieldname: "qty",
							fieldtype: "Float",
							label: "จำนวน",
							in_list_view: 1,
							reqd: 1,
						},
						{
							fieldname: "uom",
							fieldtype: "Link",
							options: "UOM",
							label: "หน่วย",
							in_list_view: 1,
						},
					],
				},
			],
			primary_action_label: "ตรวจราคาและสร้าง Preview",
			primary_action: async (values) => {
				const rows = (values.items || []).filter((row) => row.item && Number(row.qty) > 0);
				if (!rows.length) {
					frappe.msgprint("กรุณาเพิ่มสินค้าและจำนวนอย่างน้อย 1 รายการ");
					return;
				}
				const button = dialog.get_primary_btn();
				button.prop("disabled", true);
				try {
					const action = await call("prepare_sales_order_preview", {
						session_id: state.sessionId,
						customer: values.customer,
						delivery_date: values.delivery_date || null,
						items: JSON.stringify(rows),
					});
					const prepared = { ...action, action_id: action.action_id };
					state.actions.set(prepared.action_id, prepared);
					state.messages.push({
						role: "assistant",
						content: "ตรวจราคา สต๊อก และข้อมูลลูกค้าจาก ERP แล้วค่ะ กรุณาตรวจ Preview ก่อนยืนยัน",
						action: prepared,
					});
					dialog.hide();
					render();
				} catch (error) {
					frappe.msgprint(error?.message || "สร้าง Preview ไม่สำเร็จ");
				} finally {
					button.prop("disabled", false);
				}
			},
		});
		dialog.show();
	}

	function openForecastDialog(itemCode = "") {
		const dialog = new frappe.ui.Dialog({
			title: "Generate Procurement Forecast",
			fields: [
				{
					fieldname: "item",
					fieldtype: "Link",
					options: "Item",
					label: "สินค้า (เว้นว่างเพื่อสร้างทุกสินค้า)",
					default: itemCode || "",
					get_query: () => ({ filters: { disabled: 0, is_stock_item: 1, is_purchase_item: 1 } }),
				},
				{
					fieldname: "warehouse",
					fieldtype: "Link",
					options: "Warehouse",
					label: "คลัง (เว้นว่างเพื่อใช้คลังซื้อเริ่มต้น)",
					get_query: () => ({ filters: { disabled: 0, is_group: 0 } }),
				},
				{ fieldname: "horizon_days", fieldtype: "Int", label: "Horizon (Days)", default: 30, reqd: 1 },
				{ fieldname: "limit", fieldtype: "Int", label: "จำนวนสินค้าสูงสุด", default: itemCode ? 1 : 50 },
			],
			primary_action_label: "Generate Forecast",
			primary_action: async (values) => {
				const button = dialog.get_primary_btn();
				button.prop("disabled", true);
				try {
					const response = await frappe.call({
						method: "nextgen_erp.forecast.generate_forecasts",
						args: {
							item_codes: values.item ? JSON.stringify([values.item]) : null,
							warehouse: values.warehouse || null,
							horizon_days: values.horizon_days,
							limit: values.item ? 1 : values.limit,
						},
						freeze: true,
						freeze_message: "กำลังคำนวณ Forecast จากข้อมูล ERP...",
					});
					const result = response.message || {};
					dialog.hide();
					state.messages.push({
						role: "assistant",
						content: `สร้าง Forecast Snapshot แล้ว ${result.generated || 0} รายการ${result.failed ? ` · ไม่สำเร็จ ${result.failed}` : ""} โดยยังไม่ได้สร้าง PO หรือ Material Request`,
					});
					frappe.show_alert({
						message: `Generated ${result.generated || 0} forecast(s)`,
						indicator: result.failed ? "orange" : "green",
					});
					render();
				} catch (error) {
					frappe.msgprint(error?.message || "สร้าง Forecast ไม่สำเร็จ");
				} finally {
					button.prop("disabled", false);
				}
			},
		});
		dialog.show();
	}

	function editAction(actionId) {
		const action = state.actions.get(actionId);
		if (!action || action.status !== "Pending") return;
		const preview = action.preview || {};
		const isPO = action.action_type === "prepare_purchase_order" || preview.document_type === "Purchase Order";
		const fields = [
			{
				fieldname: "warehouse",
				fieldtype: "Link",
				options: "Warehouse",
				label: "คลังรับสินค้า",
				reqd: 1,
				default: preview.warehouse,
				get_query: () => ({
					filters: { company: preview.company, is_group: 0, disabled: 0 },
				}),
			},
		];
		if (isPO) {
			fields.push({
				fieldname: "supplier",
				fieldtype: "Link",
				options: "Supplier",
				label: "Supplier",
				reqd: 1,
				default: preview.supplier,
			});
		}
		fields.push({
			fieldname: "schedule_date",
			fieldtype: "Date",
			label: "กำหนดรับของ",
			reqd: 1,
			default: preview.schedule_date,
		});
		const dialog = new frappe.ui.Dialog({
			title: "แก้ไข Procurement Preview",
			fields,
			primary_action_label: "อัปเดต Preview",
			primary_action: async (values) => {
				const primary = dialog.get_primary_btn();
				primary.prop("disabled", true);
				try {
					const replacement = await call("revise_action", {
						action_id: actionId,
						changes: JSON.stringify(values),
					});
					action.status = "Cancelled";
					const nextAction = { ...replacement, action_id: replacement.action_id };
					state.actions.set(replacement.action_id, nextAction);
					for (const message of state.messages) {
						if (message.action?.action_id === actionId) message.action = nextAction;
					}
					dialog.hide();
					setStatus("อัปเดต Preview จากข้อมูล ERP แล้ว โดยไม่เรียก AI ใหม่");
					frappe.show_alert({ message: "อัปเดตคลังและ Preview แล้ว", indicator: "green" });
					render();
				} catch (error) {
					frappe.msgprint(error?.message || "อัปเดต Preview ไม่สำเร็จ");
				} finally {
					primary.prop("disabled", false);
				}
			},
		});
		dialog.show();
	}

	async function confirmAction(actionId) {
		const action = state.actions.get(actionId);
		if (!action || action.status !== "Pending") return;
		action.status = "Executing";
		render();
		try {
			const result = await call("confirm_action", { action_id: actionId });
			action.status = "Completed";
			action.result = result;
			action.result_doctype = result.document_type;
			action.result_name = result.document_name;
			const serverMessage = result.result?.message;
			const label = serverMessage
				|| (result.high_confidence ? "สร้างและจองสต๊อกสำเร็จ" : "ส่งเข้าคิวตรวจสอบแล้ว");
			state.messages.push({
				role: "assistant",
				content: `${label}: ${result.document_name}`,
				href: true,
				documentType: result.document_type,
				documentName: result.document_name,
			});
			frappe.show_alert({ message: label, indicator: "green" });
		} catch (error) {
			action.status = "Pending";
			frappe.msgprint(error?.message || "ดำเนินการไม่สำเร็จ");
		}
		render();
	}

	async function cancelAction(actionId) {
		try {
			await call("cancel_action", { action_id: actionId });
			const action = state.actions.get(actionId);
			if (action) action.status = "Cancelled";
			render();
		} catch (error) {
			frappe.msgprint(error?.message || "ยกเลิกไม่สำเร็จ");
		}
	}

	// ------------------------------------------------------------------
	// History + session restore
	// ------------------------------------------------------------------

	async function toggleHistory() {
		const panel = document.getElementById("nextgen-chat-history");
		panel.hidden = !panel.hidden;
		if (!panel.hidden) {
			panel.innerHTML = '<div class="ng-history-loading">กำลังโหลด...</div>';
			try {
				const sessions = await call("list_sessions");
				const relevant = sessions.filter(
					(session) => !state.agentKey || (session.agent_type || "sales") === state.agentKey,
				);
				panel.innerHTML = relevant.length
					? relevant.map((session) => `<button data-session="${escapeHtml(session.name)}"><strong>${escapeHtml(session.title)}</strong><small>${escapeHtml(session.last_activity_at)}</small></button>`).join("")
					: '<div class="ng-history-loading">ยังไม่มีประวัติของผู้ช่วยนี้</div>';
			} catch (error) {
				panel.innerHTML = `<div class="ng-history-loading">${escapeHtml(error?.message || "โหลดไม่สำเร็จ")}</div>`;
			}
		}
	}

	function handleHistoryClick(event) {
		const button = event.target.closest("[data-session]");
		if (button) restoreSession(button.dataset.session, true);
	}

	async function restoreSession(sessionId, notify = true) {
		try {
			const data = await call("get_session", { session_id: sessionId });
			const sessionAgent = data.session?.agent_type || "sales";
			if (sessionAgent !== state.agentKey && agentByKey(sessionAgent)) {
				// A session is pinned to its agent; follow it instead of mixing.
				state.agentKey = sessionAgent;
				window.localStorage.setItem(ACTIVE_AGENT_KEY, sessionAgent);
				applyAgentTheme(activeAgent());
			}
			state.sessionId = sessionId;
			window.localStorage.setItem(sessionStorageKey(state.agentKey), sessionId);
			state.actions.clear();
			for (const action of data.actions || []) {
				state.actions.set(action.name, { ...action, action_id: action.name });
			}
			state.messages = (data.messages || []).map((message) => ({
				role: message.role,
				content: message.content,
				forecasts: message.forecasts || [],
				action: message.action ? state.actions.get(message.action) : null,
			}));
			document.getElementById("nextgen-chat-history").hidden = true;
			render();
			if (notify) frappe.show_alert({ message: "เปิดประวัติแชตแล้ว", indicator: "blue" });
		} catch {
			if (state.sessionId === sessionId) {
				window.localStorage.removeItem(sessionStorageKey(state.agentKey));
			}
		}
	}

	// ------------------------------------------------------------------
	// Bootstrap
	// ------------------------------------------------------------------

	async function bootstrap() {
		if (frappe.session.user === "Guest") return;
		try {
			const status = await call("get_status");
			if (!status?.enabled || !(status.agents || []).length) return;
			state.brand = status.brand || "NextGen AI";
			state.agents = status.agents;
			mount();
			frappe.realtime.on("nextgen_staff_chat", onRealtime);
		} catch (error) {
			console.warn("NextGen AI assistant is unavailable", error);
			// Unauthorized roles and disabled/unavailable chat get no launcher.
		}
	}

	// Desk app_include scripts load after the Frappe bundles, often after the
	// DOM ready event has already fired. frappe.ready() belongs to the website
	// lifecycle and is not reliably triggered by Desk in Frappe v16.
	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", bootstrap, { once: true });
	} else {
		bootstrap();
	}
})();
