/* global frappe, __ */

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

	// True while the user is near the bottom of the message list; streaming
	// deltas only auto-scroll when pinned so reading history is never yanked.
	let scrollPinned = true;
	let deltaRaf = 0;

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
			content: __("ต้องการใช้ผู้ช่วยด้านไหนคะ? เลือกได้จากตัวเลือกด้านล่าง หรือเปลี่ยนภายหลังได้จากเมนูด้านบน"),
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
					? __("ถามเรื่องสต๊อก demand ความเสี่ยงของขาด หรือเตรียม PO...")
					: __("ถามข้อมูลสินค้า สต๊อก ราคา หรือสร้างออเดอร์...");
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
		render({ forceScroll: true });
		if (restore && state.sessionId) restoreSession(state.sessionId, false);
	}

	// ------------------------------------------------------------------
	// Mount + panel lifecycle
	// ------------------------------------------------------------------

	function mount() {
		if (document.getElementById("nextgen-chat-launcher")) return;
		const agentMenu = state.agents
			.map(
				(agent) => `<button type="button" role="menuitem" data-agent="${escapeHtml(agent.key)}">
					<img src="${escapeHtml(agent.icon)}" alt="">
					<span><strong>${escapeHtml(agent.title)}</strong><small>${escapeHtml(agent.subtitle || "")}</small></span>
				</button>`,
			)
			.join("");
		document.body.insertAdjacentHTML(
			"beforeend",
			`<button id="nextgen-chat-launcher" type="button" aria-label="${__("เปิด {0}", [state.brand])}" title="${escapeHtml(state.brand)}">
				<img src="${BRAND_ICON}" alt="" aria-hidden="true">
				<span class="ng-launcher-label">${escapeHtml(state.brand)}</span>
			</button>
			<aside id="nextgen-chat-panel" aria-label="${escapeHtml(state.brand)}" aria-hidden="true">
				<header class="ng-chat-header">
					<div class="ng-chat-brand">
						<img id="nextgen-agent-icon" src="${BRAND_ICON}" alt="">
						<div>
							<button id="nextgen-agent-switcher" type="button" aria-haspopup="true" aria-expanded="false" aria-controls="nextgen-agent-menu" title="${__("เปลี่ยนผู้ช่วย")}">
								<strong id="nextgen-agent-title">${escapeHtml(state.brand)}</strong>
								<span class="ng-caret" aria-hidden="true">▾</span>
							</button>
							<small id="nextgen-agent-subtitle">${escapeHtml(state.brand)} · Typhoon AI</small>
						</div>
					</div>
					<div class="ng-chat-header-actions">
						<button data-action="history" type="button" aria-label="${__("ประวัติแชต")}" title="${__("ประวัติ")}">☰</button>
						<button class="ng-new-chat-button" data-action="new" type="button" aria-label="${__("เริ่มแชตใหม่")}" title="${__("เริ่มแชตใหม่")}">
							<span aria-hidden="true">＋</span><span>${__("แชตใหม่")}</span>
						</button>
						<button data-action="close" type="button" aria-label="${__("ปิดแชต")}" title="${__("ปิด")}">×</button>
					</div>
				</header>
				<div id="nextgen-agent-menu" role="menu" hidden>${agentMenu}</div>
				<div id="nextgen-chat-history" hidden></div>
				<div id="nextgen-chat-messages" role="log" aria-live="off"></div>
				<div id="nextgen-chat-sr" class="ng-sr-only" aria-live="polite"></div>
				<div id="nextgen-chat-status" role="status"></div>
				<footer class="ng-chat-composer">
					<textarea id="nextgen-chat-input" rows="2" maxlength="4000" aria-label="${__("พิมพ์ข้อความถามผู้ช่วย")}" placeholder="${__("พิมพ์คำถาม...")}"></textarea>
					<div>
						<button id="nextgen-chat-stop" type="button" hidden>${__("หยุด")}</button>
						<button id="nextgen-chat-send" type="button">${__("ส่ง")}</button>
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

		const messages = document.getElementById("nextgen-chat-messages");
		messages.addEventListener("scroll", () => {
			scrollPinned =
				messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
		});

		// Dismiss the agent menu on outside click without swallowing the click.
		document.addEventListener("click", (event) => {
			const menu = document.getElementById("nextgen-agent-menu");
			const switcher = document.getElementById("nextgen-agent-switcher");
			if (menu.hidden) return;
			if (menu.contains(event.target) || switcher.contains(event.target)) return;
			setAgentMenuOpen(false);
		});

		// Menu keyboard support: Escape closes, arrows move between agents.
		document.getElementById("nextgen-agent-menu").addEventListener("keydown", (event) => {
			const items = [...event.currentTarget.querySelectorAll("[data-agent]")];
			const index = items.indexOf(document.activeElement);
			if (event.key === "Escape") {
				event.preventDefault();
				setAgentMenuOpen(false);
				document.getElementById("nextgen-agent-switcher").focus();
			} else if (event.key === "ArrowDown") {
				event.preventDefault();
				items[(index + 1) % items.length]?.focus();
			} else if (event.key === "ArrowUp") {
				event.preventDefault();
				items[(index - 1 + items.length) % items.length]?.focus();
			}
		});

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
		const hadFocus = panel?.contains(document.activeElement);
		panel?.classList.remove("is-open");
		panel?.setAttribute("aria-hidden", "true");
		setAgentMenuOpen(false);
		// Return focus to the launcher so keyboard users keep their place.
		if (hadFocus) document.getElementById("nextgen-chat-launcher")?.focus();
	}

	function setAgentMenuOpen(openMenu, { focusFirst = false } = {}) {
		const menu = document.getElementById("nextgen-agent-menu");
		const switcher = document.getElementById("nextgen-agent-switcher");
		if (!menu || !switcher) return;
		menu.hidden = !openMenu;
		switcher.setAttribute("aria-expanded", String(openMenu));
		if (openMenu && focusFirst) menu.querySelector("[data-agent]")?.focus();
	}

	function toggleAgentMenu() {
		const menu = document.getElementById("nextgen-agent-menu");
		setAgentMenuOpen(menu.hidden, { focusFirst: menu.hidden });
	}

	function handleAgentMenuClick(event) {
		const button = event.target.closest("[data-agent]");
		if (!button) return;
		setAgentMenuOpen(false);
		if (button.dataset.agent !== state.agentKey) selectAgent(button.dataset.agent);
		document.getElementById("nextgen-chat-input")?.focus();
	}

	function newChat() {
		if (state.turnId) return;
		const agent = activeAgent();
		state.sessionId = null;
		state.lastInput = "";
		state.actions.clear();
		if (state.agentKey) window.localStorage.removeItem(sessionStorageKey(state.agentKey));
		state.messages = agent ? [welcomeMessage(agent)] : [agentChooserMessage()];
		document.getElementById("nextgen-chat-history").hidden = true;
		setAgentMenuOpen(false);
		document.getElementById("nextgen-chat-input").value = "";
		setStatus("");
		render();
		document.getElementById("nextgen-chat-input").focus();
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
		const pending = { role: "assistant", content: "", pending: true, action: null, forecasts: [], comparisons: [] };
		state.messages.push(pending);
		state.turnId = makeTurnId();
		pending.turnId = state.turnId;
		setBusy(true, __("Typhoon กำลังตรวจข้อมูล ERP..."));
		render({ forceScroll: true });
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
			pending.content = error?.message || __("ไม่สามารถเริ่มแชตได้");
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
					pending.comparisons = saved.comparisons || pending.comparisons || [];
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
					announce(pending.content);
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
					pending.content ||= __("คำตอบใช้เวลานานกว่าปกติ กรุณาลองอีกครั้งค่ะ");
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
			pending.content ||= __("หยุดแสดงคำตอบแล้ว งานฝั่งเซิร์ฟเวอร์จะสิ้นสุดอย่างปลอดภัยค่ะ");
		}
		setBusy(false);
		render();
		announce(pending?.content);
	}

	function onRealtime(event) {
		if (!event?.turn_id || event.turn_id === state.ignoredTurn) return;
		if (event.turn_id !== state.turnId) return;
		const pending = [...state.messages].reverse().find((message) => message.pending);
		if (!pending) return;
		if (event.type === "delta") {
			pending.content += event.text || "";
			scheduleDeltaRender(pending);
		} else if (event.type === "tool") {
			setStatus(__("กำลังใช้เครื่องมือ: {0}", [event.name]));
		} else if (event.type === "action") {
			pending.action = event.action;
			state.actions.set(event.action.action_id, event.action);
			render();
		} else if (event.type === "forecast") {
			pending.forecasts = [...(pending.forecasts || []), event.forecast];
			render();
		} else if (event.type === "comparison") {
			pending.comparisons = [...(pending.comparisons || []), event.comparison];
			render();
		} else if (event.type === "done") {
			pending.pending = false;
			state.turnId = null;
			setBusy(false);
			render();
			announce(pending.content);
		} else if (event.type === "error") {
			pending.pending = false;
			pending.error = true;
			pending.content ||= event.message || __("ผู้ช่วยไม่พร้อมใช้งาน");
			state.turnId = null;
			setBusy(false);
			render();
			announce(pending.content);
		}
	}

	function setBusy(busy, text = "") {
		document.getElementById("nextgen-chat-send").disabled = busy;
		document.getElementById("nextgen-chat-input").disabled = busy;
		document.querySelector('[data-action="new"]').disabled = busy;
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

	// Shared shell for every actionable card: title bar, body, warnings,
	// status-dependent button row, and the result/document link line.
	function cardShell({ extraClass = "", id, title, badge = "", body = "", warningsHtml = "", buttonsHtml = "", statusText = "", resultType = null, resultName = null }) {
		const resultLink = resultType && resultName
			? ` · <button type="button" class="ng-doc-link" data-document-type="${escapeHtml(resultType)}" data-document-name="${escapeHtml(resultName)}">${escapeHtml(resultName)}</button>`
			: "";
		return `<section class="ng-action-card ${extraClass}" data-action-id="${escapeHtml(id)}">
			<div class="ng-action-title"><strong>${title}</strong>${badge}</div>
			${body}
			${warningsHtml}
			${buttonsHtml}
			<div class="ng-action-result">${statusText ? escapeHtml(statusText) : ""}${resultLink}</div>
		</section>`;
	}

	function riskBadge(risk) {
		const labels = { high: __("เสี่ยงสูง"), medium: __("เสี่ยงปานกลาง"), low: __("เสี่ยงต่ำ") };
		return `<span class="ng-risk ng-risk-${escapeHtml(risk || "low")}">${labels[risk] || escapeHtml(risk || "-")}</span>`;
	}

	function forecastCard(forecast) {
		if (!forecast) return "";
		const warnings = (forecast.warnings || [])
			.map((warning) => `<li>${escapeHtml(warning)}</li>`)
			.join("");
		const rows = [
			[__("สต๊อกปัจจุบัน"), `${num(forecast.actual_qty)} ${escapeHtml(forecast.stock_uom || "")}`],
			[__("จองแล้ว"), num(forecast.reserved_qty)],
			[__("กำลังมา (PO)"), num(forecast.incoming_qty)],
			[__("Demand 30/60/90 วัน"), `${num(forecast.demand_30)} / ${num(forecast.demand_60)} / ${num(forecast.demand_90)}`],
			[__("เฉลี่ยต่อวัน"), num(forecast.average_daily_demand, 2)],
			[__("Lead time"), __("{0} วัน", [num(forecast.lead_time_days)])],
			[__("Days of supply"), forecast.days_of_supply == null ? "-" : __("{0} วัน", [num(forecast.days_of_supply, 1)])],
			[__("Reorder point"), num(forecast.reorder_point, 1)],
			[__("จำนวนแนะนำ"), `<strong>${num(forecast.suggested_qty)} ${escapeHtml(forecast.stock_uom || "")}</strong>`],
			[__("คาดว่าจะขาด"), forecast.stockout_date ? escapeHtml(forecast.stockout_date) : "-"],
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
			<div class="ng-forecast-sub">${escapeHtml(forecast.item_code)} · ${__("Horizon {0} วัน", [num(forecast.horizon_days)])} · Data quality ${Math.round(Number(forecast.data_quality_score || 0) * 100)}%</div>
			<div class="ng-forecast-grid">${rows}</div>
			${warnings ? `<ul class="ng-action-warnings">${warnings}</ul>` : ""}
			<div class="ng-forecast-footer">${escapeHtml(forecast.formula_version || "")}</div>
			<div class="ng-message-actions">
				<button type="button" data-generate-forecast="${itemCode}">${__("บันทึก Forecast")}</button>
				<button type="button" data-chat-prompt="สร้าง Purchase Order Preview สำหรับ ${itemCode} จำนวน ${qty} ${uom} จาก Forecast ล่าสุด">${__("สร้าง PO Preview")}</button>
				<button type="button" data-chat-prompt="สร้าง Material Request Preview สำหรับ ${itemCode} จำนวน ${qty} ${uom} จาก Forecast ล่าสุด">${__("สร้าง MR Preview")}</button>
			</div>
		</section>`;
	}

	function comparisonCard(data) {
		if (!data || !(data.suppliers || []).length) return "";
		const rows = (data.suppliers || [])
			.map((row) => {
				const premium = Number(row.price_premium_percent || 0);
				const premiumLabel = premium > 0
					? `<small class="ng-var-up">+${premium.toFixed(2)}%</small>`
					: (row.last_rate > 0 ? `<small class="ng-var-down">${__("ต่ำสุด")}</small>` : "");
				const onTime = row.on_time_rate == null
					? __("ไม่มีข้อมูล")
					: `${Math.round(Number(row.on_time_rate) * 100)}%`;
				const leadNote = row.measured_lead_days == null ? ` ${__("(ประมาณ)")}` : "";
				return `<tr class="${row.recommended ? "ng-vendor-recommended" : ""}">
					<td>${row.recommended ? "★ " : ""}${escapeHtml(row.supplier_name || row.supplier)}<small>${escapeHtml(row.supplier)}</small></td>
					<td>${row.last_rate > 0 ? num(row.last_rate, 2) : "-"} ${premiumLabel}</td>
					<td>${__("{0} วัน", [num(row.effective_lead_days, 1)])}${leadNote}</td>
					<td>${onTime}</td>
					<td>${num(row.order_count)}</td>
					<td><button type="button" class="ng-doc-link" data-use-supplier="${escapeHtml(row.supplier)}" data-use-item="${escapeHtml(data.item_code || "")}">${__("ใช้รายนี้")}</button></td>
				</tr>`;
			})
			.join("");
		const recommended = data.recommended || {};
		return `<section class="ng-forecast-card ng-comparison-card">
			<div class="ng-forecast-title">
				<strong>${__("เปรียบเทียบ Supplier: {0}", [escapeHtml(data.item_name || data.item_code)])}</strong>
				<span class="ng-priority-chip ng-priority-${escapeHtml(data.priority || "balanced")}">${escapeHtml(data.priority_label || __("ปกติ"))}</span>
			</div>
			<div class="ng-comparison-scroll"><table><thead>
				<tr><th>Supplier</th><th>${__("ราคาล่าสุด")}</th><th>${__("Lead จริง")}</th><th>${__("ตรงเวลา")}</th><th>${__("ออเดอร์")}</th><th></th></tr>
			</thead><tbody>${rows}</tbody></table></div>
			${recommended.reason ? `<div class="ng-priority-note">${escapeHtml(recommended.reason)}</div>` : ""}
			<div class="ng-forecast-footer">${__("คำนวณจาก PO และการรับของจริงใน ERP เท่านั้น")}</div>
		</section>`;
	}

	function applyActionReplacement(actionId, replacement) {
		const action = state.actions.get(actionId);
		if (action) action.status = "Cancelled";
		const nextAction = { ...replacement, action_id: replacement.action_id };
		state.actions.set(replacement.action_id, nextAction);
		for (const message of state.messages) {
			if (message.action?.action_id === actionId) message.action = nextAction;
		}
		render();
	}

	function latestPendingProcurementAction(actionType) {
		let found = null;
		for (const action of state.actions.values()) {
			if (action.status === "Pending" && action.action_type === actionType) found = action;
		}
		return found;
	}

	async function useSupplier(supplier, itemCode) {
		const pendingPO = latestPendingProcurementAction("prepare_purchase_order");
		if (!pendingPO) {
			// No preview to edit yet: ask the assistant to prepare one instead.
			return send(`สร้าง Purchase Order preview ของ ${itemCode || "สินค้านี้"} จาก supplier ${supplier}`);
		}
		try {
			const replacement = await call("revise_action", {
				action_id: pendingPO.action_id,
				changes: JSON.stringify({ supplier }),
			});
			applyActionReplacement(pendingPO.action_id, replacement);
			frappe.show_alert({ message: __("เปลี่ยน supplier เป็น {0} แล้ว", [supplier]), indicator: "green" });
		} catch (error) {
			frappe.msgprint(error?.message || __("เปลี่ยน supplier ไม่สำเร็จ"));
		}
	}

	function contextualActions(message, index) {
		if (
			message.role !== "assistant"
			|| message.pending
			|| message.error
			|| message.action
			|| (message.forecasts || []).length
			|| (message.comparisons || []).length
			|| index === 0
		) return "";
		if (state.agentKey === "procurement") {
			return `<div class="ng-message-actions ng-inline-actions">
				<button type="button" data-generate-forecast="">Generate Forecast</button>
				<button type="button" data-chat-prompt="สร้าง Purchase Order Preview จากคำแนะนำล่าสุด โดยใช้ข้อมูล ERP ปัจจุบัน">${__("สร้าง PO Preview")}</button>
				<button type="button" data-chat-prompt="สร้าง Material Request Preview จากคำแนะนำล่าสุด โดยใช้ข้อมูล ERP ปัจจุบัน">${__("สร้าง MR Preview")}</button>
			</div>`;
		}
		return `<div class="ng-message-actions ng-inline-actions">
			<button type="button" data-start-sales-order="1">${__("เริ่มสร้างออเดอร์")}</button>
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
				const manualTag = item.rate_overridden
					? `<small class="ng-manual-rate">${__("กำหนดเอง")}</small>`
					: "";
				return `<tr>
					<td>${escapeHtml(item.item_code)}<small>${escapeHtml(item.item_name || "")}${constraint ? ` · ${constraint}` : ""}</small></td>
					<td>${num(item.qty)} ${escapeHtml(item.uom || "")}<small>${__("แนะนำ {0}", [num(item.suggested_qty)])}</small></td>
					<td>${num(item.rate, 2)} ${manualTag}<small>${__("ล่าสุด {0}", [num(item.last_purchase_rate, 2)])} ${varianceLabel}</small></td>
					<td>${num(item.amount, 2)}</td>
				</tr>`;
			})
			.join("");
		const warnings = (preview.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
		const status = action.status || "Pending";
		const result = action.result || {};
		const resultType = action.result_doctype || result.document_type;
		const resultName = action.result_name || result.document_name;
		const priorityChip = preview.priority && preview.priority !== "balanced"
			? `<span class="ng-priority-chip ng-priority-${escapeHtml(preview.priority)}">${escapeHtml(preview.priority_label || preview.priority)}</span>`
			: "";
		const autoPicked = preview.supplier_auto_selected
			? ` <small class="ng-auto-picked">${__("(ระบบเลือกให้)")}</small>`
			: "";
		return cardShell({
			extraClass: "ng-procurement-card",
			id: action.action_id,
			title,
			badge: `${priorityChip}<span>Data quality ${Math.round(Number(preview.data_quality_score ?? action.confidence ?? 0) * 100)}%</span>`,
			body: `
				<div class="ng-action-customer">
					${isPO ? `Supplier: <strong>${escapeHtml(preview.supplier_name || preview.supplier || "-")}</strong>${autoPicked}<br>` : ""}
					${__("บริษัท")}: ${escapeHtml(preview.company || "-")} · ${__("คลัง")}: ${escapeHtml(preview.warehouse || "-")}<br>
					${__("กำหนดรับของ")}: ${escapeHtml(preview.schedule_date || "-")} · ${__("โหมด")}: ${escapeHtml(preview.automation_mode || "-")}
				</div>
				${preview.priority_note ? `<div class="ng-priority-note">${escapeHtml(preview.priority_note)}</div>` : ""}
				<table><tbody>${items}</tbody></table>
				<div class="ng-action-total">${__("รวม {0} THB", [num(preview.total, 2)])}</div>
				${preview.forecast_explanation ? `<div class="ng-forecast-footer">${escapeHtml(preview.forecast_explanation)}</div>` : ""}`,
			warningsHtml: warnings ? `<ul class="ng-action-warnings">${warnings}</ul>` : "",
			buttonsHtml: `<div class="ng-action-buttons" ${status !== "Pending" ? "hidden" : ""}>
				<button type="button" data-confirm-action="${escapeHtml(action.action_id)}">${__("ยืนยันและสร้าง")}</button>
				<button type="button" class="secondary" data-edit-action="${escapeHtml(action.action_id)}">${__("แก้ข้อมูล")}</button>
				<button type="button" class="secondary" data-cancel-action="${escapeHtml(action.action_id)}">${__("ยกเลิก")}</button>
			</div>`,
			statusText: status !== "Pending" ? status : "",
			resultType,
			resultName,
		});
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
		return cardShell({
			id: action.action_id,
			title: "Sales Order Preview",
			badge: `<span>${Math.round(Number(preview.confidence || 0) * 100)}%</span>`,
			body: `
				<div class="ng-action-customer">${__("ลูกค้า")}: <strong>${escapeHtml(preview.customer_name || preview.customer)}</strong></div>
				<table><tbody>${items}</tbody></table>
				<div class="ng-action-total">${__("รวม {0} THB", [num(preview.total, 2)])}</div>`,
			warningsHtml: warnings ? `<ul class="ng-action-warnings">${warnings}</ul>` : "",
			buttonsHtml: `<div class="ng-action-buttons" ${status !== "Pending" ? "hidden" : ""}>
				<button type="button" data-confirm-action="${escapeHtml(action.action_id)}">${__("ยืนยันและดำเนินการ")}</button>
				<button type="button" class="secondary" data-cancel-action="${escapeHtml(action.action_id)}">${__("ยกเลิก")}</button>
			</div>`,
			statusText: status !== "Pending" ? status : "",
			resultType,
			resultName,
		});
	}

	function documentActionCard(action) {
		const preview = action.preview || {};
		const values = preview.values || {};
		const rows = Object.keys(values)
			.map(
				(field) =>
					`<tr><td>${escapeHtml(field)}</td><td>${escapeHtml(String(values[field] ?? ""))}</td></tr>`,
			)
			.join("");
		const warnings = (action.warnings || preview.warnings || [])
			.map((warning) => `<li>${escapeHtml(warning)}</li>`)
			.join("");
		const status = action.status || "Pending Approval";
		const result = action.result || {};
		const resultType = action.result_doctype || result.document_type;
		const resultName = action.result_name || result.document_name;
		// The proposal ID is the identifier here; there is no legacy chat action.
		const id = action.proposal_id || action.action_id;
		const open = status === "Pending Approval" || status === "Pending";
		return cardShell({
			extraClass: "ng-document-card",
			id,
			title: escapeHtml(preview.doctype || "Document"),
			badge: `<span>${__("ข้อเสนอ")}</span>`,
			body: `
				<div class="ng-action-customer">
					${__("บริษัท")}: ${escapeHtml(preview.company || "-")}${preview.purpose ? `<br>${escapeHtml(preview.purpose)}` : ""}
				</div>
				<table><tbody>${rows}</tbody></table>
				<div class="ng-action-total">${__("ยังไม่บันทึกลง ERP จนกว่าคุณจะกดอนุมัติ")}</div>`,
			warningsHtml: warnings ? `<ul class="ng-action-warnings">${warnings}</ul>` : "",
			buttonsHtml: `<div class="ng-action-buttons" ${open ? "" : "hidden"}>
				<button type="button" data-approve-proposal="${escapeHtml(id)}">${__("อนุมัติและสร้าง")}</button>
				<button type="button" class="secondary" data-reject-proposal="${escapeHtml(id)}">${__("ปฏิเสธ")}</button>
			</div>`,
			statusText: !open ? status : "",
			resultType,
			resultName,
		});
	}

	function actionCard(action) {
		if (!action?.preview) return "";
		const type = action.action_type || "prepare_sales_order";
		if (type === "prepare_document") {
			return documentActionCard(action);
		}
		if (type === "prepare_purchase_order" || type === "prepare_material_request") {
			return procurementActionCard(action);
		}
		return salesActionCard(action);
	}

	function render({ forceScroll = false } = {}) {
		const container = document.getElementById("nextgen-chat-messages");
		if (!container) return;
		container.innerHTML = state.messages
			.map((message, index) => {
				const suggestions = (message.suggestions || [])
					.map((value) => `<button type="button" data-suggestion="${escapeHtml(value)}">${escapeHtml(value)}</button>`)
					.join("");
				const agentChoices = (message.agentChoices || [])
					.map((key) => {
						const agent = agentByKey(key);
						if (!agent) return "";
						return `<button type="button" class="ng-agent-choice" data-choose-agent="${escapeHtml(key)}">
							<img src="${escapeHtml(agent.icon)}" alt=""> ${escapeHtml(agent.title)}
						</button>`;
					})
					.join("");
				const forecasts = (message.forecasts || []).map(forecastCard).join("");
				const comparisons = (message.comparisons || []).map(comparisonCard).join("");
				const documentLink = message.href ? ` <button type="button" class="ng-doc-link" data-document-type="${escapeHtml(message.documentType)}" data-document-name="${escapeHtml(message.documentName)}">${__("เปิดเอกสาร")}</button>` : "";
				const contextual = contextualActions(message, index);
				return `<div class="ng-message ng-${message.role} ${message.error ? "ng-error" : ""}" data-index="${index}">
					<div class="ng-bubble">${message.pending && !message.content ? `<span class="ng-thinking">${__("กำลังคิด")}</span>` : renderText(message.content)}${documentLink}</div>
					${forecasts}
					${comparisons}
					${message.action ? actionCard(message.action) : ""}
					${contextual}
					${suggestions ? `<div class="ng-suggestions">${suggestions}</div>` : ""}
					${agentChoices ? `<div class="ng-suggestions ng-agent-choices">${agentChoices}</div>` : ""}
					${message.error ? `<button type="button" data-retry="1" class="ng-retry">${__("ลองอีกครั้ง")}</button>` : ""}
				</div>`;
			})
			.join("");
		scrollToBottom(forceScroll);
	}

	function scrollToBottom(force = false) {
		if (!force && !scrollPinned) return;
		const container = document.getElementById("nextgen-chat-messages");
		if (container) container.scrollTop = container.scrollHeight;
	}

	// Streaming deltas update only the pending bubble's content, batched once
	// per animation frame — no full-list rebuilds, no scroll hijack, and screen
	// readers are not asked to re-announce the whole log on every token.
	function scheduleDeltaRender(pending) {
		if (deltaRaf) return;
		deltaRaf = window.requestAnimationFrame(() => {
			deltaRaf = 0;
			const container = document.getElementById("nextgen-chat-messages");
			const bubble = container?.lastElementChild?.querySelector(".ng-bubble");
			if (bubble && pending.content) bubble.innerHTML = renderText(pending.content);
			scrollToBottom();
		});
	}

	// Completed assistant replies are announced once through a dedicated polite
	// region, instead of live-announcing every streaming fragment.
	function announce(text) {
		const region = document.getElementById("nextgen-chat-sr");
		if (!region || !text) return;
		region.textContent = "";
		window.setTimeout(() => {
			region.textContent = String(text).slice(0, 800);
		}, 50);
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
		const approve = event.target.closest("[data-approve-proposal]");
		if (approve) return approveProposal(approve.dataset.approveProposal);
		const reject = event.target.closest("[data-reject-proposal]");
		if (reject) return rejectProposal(reject.dataset.rejectProposal);
		const useSup = event.target.closest("[data-use-supplier]");
		if (useSup) return useSupplier(useSup.dataset.useSupplier, useSup.dataset.useItem);
		const link = event.target.closest("[data-document-type]");
		if (link) frappe.set_route("Form", link.dataset.documentType, link.dataset.documentName);
	}

	function openSalesOrderDialog() {
		if (!state.sessionId || state.agentKey !== "sales") {
			frappe.msgprint(__("กรุณาเปิด AI Sales Copilot และเริ่มแชตก่อนค่ะ"));
			return;
		}
		const dialog = new frappe.ui.Dialog({
			title: __("สร้าง Sales Order Preview"),
			fields: [
				{
					fieldname: "customer",
					fieldtype: "Link",
					options: "Customer",
					label: __("ลูกค้า"),
					reqd: 1,
				},
				{
					fieldname: "delivery_date",
					fieldtype: "Date",
					label: __("วันที่ต้องการส่ง"),
				},
				{
					fieldname: "items",
					fieldtype: "Table",
					label: __("สินค้า"),
					reqd: 1,
					in_place_edit: true,
					cannot_add_rows: false,
					data: [],
					fields: [
						{
							fieldname: "item",
							fieldtype: "Link",
							options: "Item",
							label: __("สินค้า"),
							in_list_view: 1,
							reqd: 1,
							get_query: () => ({ filters: { disabled: 0, is_sales_item: 1 } }),
						},
						{
							fieldname: "qty",
							fieldtype: "Float",
							label: __("จำนวน"),
							in_list_view: 1,
							reqd: 1,
						},
						{
							fieldname: "uom",
							fieldtype: "Link",
							options: "UOM",
							label: __("หน่วย"),
							in_list_view: 1,
						},
					],
				},
			],
			primary_action_label: __("ตรวจราคาและสร้าง Preview"),
			primary_action: async (values) => {
				const rows = (values.items || []).filter((row) => row.item && Number(row.qty) > 0);
				if (!rows.length) {
					frappe.msgprint(__("กรุณาเพิ่มสินค้าและจำนวนอย่างน้อย 1 รายการ"));
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
						content: __("ตรวจราคา สต๊อก และข้อมูลลูกค้าจาก ERP แล้วค่ะ กรุณาตรวจ Preview ก่อนยืนยัน"),
						action: prepared,
					});
					dialog.hide();
					render();
				} catch (error) {
					frappe.msgprint(error?.message || __("สร้าง Preview ไม่สำเร็จ"));
				} finally {
					button.prop("disabled", false);
				}
			},
		});
		dialog.show();
	}

	function openForecastDialog(itemCode = "") {
		const dialog = new frappe.ui.Dialog({
			title: __("Generate Procurement Forecast"),
			fields: [
				{
					fieldname: "item",
					fieldtype: "Link",
					options: "Item",
					label: __("สินค้า (เว้นว่างเพื่อสร้างทุกสินค้า)"),
					default: itemCode || "",
					get_query: () => ({ filters: { disabled: 0, is_stock_item: 1, is_purchase_item: 1 } }),
				},
				{
					fieldname: "warehouse",
					fieldtype: "Link",
					options: "Warehouse",
					label: __("คลัง (เว้นว่างเพื่อใช้คลังซื้อเริ่มต้น)"),
					get_query: () => ({ filters: { disabled: 0, is_group: 0 } }),
				},
				{ fieldname: "horizon_days", fieldtype: "Int", label: __("Horizon (Days)"), default: 30, reqd: 1 },
				{ fieldname: "limit", fieldtype: "Int", label: __("จำนวนสินค้าสูงสุด"), default: itemCode ? 1 : 50 },
			],
			primary_action_label: __("Generate Forecast"),
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
						freeze_message: __("กำลังคำนวณ Forecast จากข้อมูล ERP..."),
					});
					const result = response.message || {};
					dialog.hide();
					state.messages.push({
						role: "assistant",
						content: __("สร้าง Forecast Snapshot แล้ว {0} รายการ{1} โดยยังไม่ได้สร้าง PO หรือ Material Request", [
							result.generated || 0,
							result.failed ? ` · ${__("ไม่สำเร็จ {0}", [result.failed])}` : "",
						]),
					});
					frappe.show_alert({
						message: __(`Generated ${result.generated || 0} forecast(s)`),
						indicator: result.failed ? "orange" : "green",
					});
					render();
				} catch (error) {
					frappe.msgprint(error?.message || __("สร้าง Forecast ไม่สำเร็จ"));
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
		const lines = preview.items || [];
		const originalPriority = preview.priority || "balanced";
		const fields = [
			{
				fieldname: "warehouse",
				fieldtype: "Link",
				options: "Warehouse",
				label: __("คลังรับสินค้า"),
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
				label: __("Supplier"),
				reqd: 1,
				default: preview.supplier,
			});
			fields.push({
				fieldname: "priority",
				fieldtype: "Select",
				label: __("ความเร่งด่วน"),
				options: [
					{ label: __("ปกติ (สมดุลราคา/ความเร็ว)"), value: "balanced" },
					{ label: __("ด่วน (เน้นเร็ว)"), value: "urgent" },
					{ label: __("เน้นราคาถูก"), value: "best_price" },
				],
				default: originalPriority,
				description: __("เปลี่ยนแล้วระบบจะจัดวันรับของและคำเตือนใหม่ตามลำดับความสำคัญ"),
			});
		}
		fields.push({
			fieldname: "schedule_date",
			fieldtype: "Date",
			label: __("กำหนดรับของ"),
			reqd: 1,
			default: preview.schedule_date,
		});
		if (lines.length) {
			fields.push({ fieldname: "sb_items", fieldtype: "Section Break", label: __("รายการสินค้า") });
			lines.forEach((line, index) => {
				fields.push({
					fieldname: `qty__${index}`,
					fieldtype: "Float",
					label: __("จำนวน: {0} ({1})", [line.item_name || line.item_code, line.uom || ""]),
					reqd: 1,
					default: line.qty,
				});
				fields.push({
					fieldname: `rate__${index}`,
					fieldtype: "Currency",
					label: __("ราคา/หน่วย: {0}{1}", [line.item_code, line.rate_overridden ? ` ${__("(กำหนดเองอยู่)")}` : ""]),
					default: line.rate,
					description: __("แก้เฉพาะเมื่อตกลงราคากับ supplier เอง ระบบจะบันทึกว่าเป็นราคากำหนดเอง"),
				});
			});
		}
		const dialog = new frappe.ui.Dialog({
			title: __("แก้ไข Procurement Preview"),
			fields,
			primary_action_label: __("อัปเดต Preview"),
			primary_action: async (values) => {
				// Send only what actually changed so an untouched supplier/priority
				// keeps the server free to re-rank, and untouched rates stay system rates.
				const changes = {};
				if (values.warehouse && values.warehouse !== preview.warehouse) changes.warehouse = values.warehouse;
				if (values.schedule_date && values.schedule_date !== preview.schedule_date) {
					changes.schedule_date = values.schedule_date;
				}
				if (isPO && values.supplier && values.supplier !== preview.supplier) changes.supplier = values.supplier;
				if (isPO && values.priority && values.priority !== originalPriority) changes.priority = values.priority;
				const itemChanges = [];
				lines.forEach((line, index) => {
					const qty = Number(values[`qty__${index}`]);
					const rate = Number(values[`rate__${index}`]);
					const qtyChanged = Number.isFinite(qty) && Math.abs(qty - Number(line.qty)) > 1e-9;
					const rateChanged = Number.isFinite(rate) && Math.abs(rate - Number(line.rate)) > 1e-9;
					if (!qtyChanged && !rateChanged) return;
					const entry = { item_code: line.item_code, qty: qtyChanged ? qty : Number(line.qty) };
					if (rateChanged) entry.rate = rate;
					itemChanges.push(entry);
				});
				if (itemChanges.length) changes.items = itemChanges;
				if (!Object.keys(changes).length) {
					dialog.hide();
					return;
				}
				const primary = dialog.get_primary_btn();
				primary.prop("disabled", true);
				try {
					const replacement = await call("revise_action", {
						action_id: actionId,
						changes: JSON.stringify(changes),
					});
					applyActionReplacement(actionId, replacement);
					dialog.hide();
					setStatus(__("อัปเดต Preview จากข้อมูล ERP แล้ว โดยไม่เรียก AI ใหม่"));
					frappe.show_alert({ message: __("อัปเดต Preview แล้ว"), indicator: "green" });
				} catch (error) {
					frappe.msgprint(error?.message || __("อัปเดต Preview ไม่สำเร็จ"));
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
				|| (result.high_confidence ? __("สร้างและจองสต๊อกสำเร็จ") : __("ส่งเข้าคิวตรวจสอบแล้ว"));
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
			frappe.msgprint(error?.message || __("ดำเนินการไม่สำเร็จ"));
		}
		render();
	}

	const GATEWAY = "nextgen_erp.agent_gateway.api.";

	async function approveProposal(proposalId) {
		const action = state.actions.get(proposalId);
		if (action) {
			action.status = "Executing";
			render();
		}
		try {
			// Approval is checked against the snapshot the card was rendered
			// from; a stale preview is rejected rather than silently executed.
			await frappe.call({
				method: `${GATEWAY}approve_proposal`,
				args: { proposal_id: proposalId, snapshot_hash: action?.snapshot_hash },
			});
			const response = await frappe.call({
				method: `${GATEWAY}execute_proposal`,
				args: { proposal_id: proposalId },
			});
			const result = response.message || {};
			if (action) {
				action.status = "Completed";
				action.result = result;
				action.result_doctype = result.document_type;
				action.result_name = result.document_name;
			}
			state.messages.push({
				role: "assistant",
				content: __("สร้าง {0} แล้ว: {1}", [result.document_type, result.document_name]),
				href: true,
				documentType: result.document_type,
				documentName: result.document_name,
			});
			frappe.show_alert({ message: __("สร้างเอกสารสำเร็จ"), indicator: "green" });
		} catch (error) {
			if (action) action.status = "Pending Approval";
			frappe.msgprint(error?.message || __("อนุมัติไม่สำเร็จ"));
		}
		render();
	}

	async function rejectProposal(proposalId) {
		try {
			await frappe.call({
				method: `${GATEWAY}reject_proposal`,
				args: { proposal_id: proposalId },
			});
			const action = state.actions.get(proposalId);
			if (action) action.status = "Rejected";
			render();
		} catch (error) {
			frappe.msgprint(error?.message || __("ปฏิเสธไม่สำเร็จ"));
		}
	}

	async function cancelAction(actionId) {
		try {
			await call("cancel_action", { action_id: actionId });
			const action = state.actions.get(actionId);
			if (action) action.status = "Cancelled";
			render();
		} catch (error) {
			frappe.msgprint(error?.message || __("ยกเลิกไม่สำเร็จ"));
		}
	}

	// ------------------------------------------------------------------
	// History + session restore
	// ------------------------------------------------------------------

	async function toggleHistory() {
		const panel = document.getElementById("nextgen-chat-history");
		panel.hidden = !panel.hidden;
		if (!panel.hidden) {
			panel.innerHTML = `<div class="ng-history-loading">${__("กำลังโหลด...")}</div>`;
			try {
				const sessions = await call("list_sessions");
				const relevant = sessions.filter(
					(session) => !state.agentKey || (session.agent_type || "sales") === state.agentKey,
				);
				panel.innerHTML = relevant.length
					? relevant.map((session) => `<button type="button" data-session="${escapeHtml(session.name)}"><strong>${escapeHtml(session.title)}</strong><small>${escapeHtml(session.last_activity_at)}</small></button>`).join("")
					: `<div class="ng-history-loading">${__("ยังไม่มีประวัติของผู้ช่วยนี้")}</div>`;
			} catch (error) {
				panel.innerHTML = `<div class="ng-history-loading">${escapeHtml(error?.message || __("โหลดไม่สำเร็จ"))}</div>`;
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
				comparisons: message.comparisons || [],
				action: message.action ? state.actions.get(message.action) : null,
			}));
			document.getElementById("nextgen-chat-history").hidden = true;
			render({ forceScroll: true });
			if (notify) frappe.show_alert({ message: __("เปิดประวัติแชตแล้ว"), indicator: "blue" });
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
