/* global frappe */

(() => {
	"use strict";

	const METHOD = "nextgen_erp.staff_chat.";
	const STORAGE_KEY = "nextgen_staff_chat_session";
	const COPILOT_ICON = "/assets/nextgen_erp/images/ai-sales-copilot.svg";
	const state = {
		open: false,
		sessionId: window.localStorage.getItem(STORAGE_KEY),
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

	function mount() {
		if (document.getElementById("nextgen-chat-launcher")) return;
		document.body.insertAdjacentHTML(
			"beforeend",
			`<button id="nextgen-chat-launcher" type="button" aria-label="เปิด AI Sales Copilot" title="AI Sales Copilot">
				<img src="${COPILOT_ICON}" alt="" aria-hidden="true">
				<span class="ng-launcher-label">AI Sales Copilot</span>
			</button>
			<aside id="nextgen-chat-panel" aria-label="NextGen Staff Chat" aria-hidden="true">
				<header class="ng-chat-header">
					<div class="ng-chat-brand"><img src="${COPILOT_ICON}" alt=""><div><strong>AI Sales Copilot</strong><small>NextGen ERP · Typhoon AI</small></div></div>
					<div class="ng-chat-header-actions">
						<button data-action="history" title="ประวัติ">☰</button>
						<button data-action="new" title="แชตใหม่">＋</button>
						<button data-action="close" title="ปิด">×</button>
					</div>
				</header>
				<div id="nextgen-chat-history" hidden></div>
				<div id="nextgen-chat-messages" role="log" aria-live="polite"></div>
				<div id="nextgen-chat-status"></div>
				<footer class="ng-chat-composer">
					<textarea id="nextgen-chat-input" rows="2" maxlength="4000" placeholder="ถามข้อมูลสินค้า สต๊อก ราคา หรือสร้างออเดอร์..."></textarea>
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

		state.messages = [
			{
				role: "assistant",
				content: "สวัสดีค่ะ ฉันช่วยค้นหาสินค้า ราคา สต๊อก และเตรียม Sales Order ให้ตรวจสอบได้",
				suggestions: ["สินค้าตัวไหนสต๊อกต่ำ", "ดูออเดอร์ล่าสุด", "สร้าง Sales Order"],
			},
		];
		render();
		if (state.sessionId) restoreSession(state.sessionId, false);
	}

	function toggle() {
		state.open ? close() : open();
	}

	function open() {
		state.open = true;
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
	}

	function newChat() {
		state.sessionId = null;
		state.turnId = null;
		state.actions.clear();
		window.localStorage.removeItem(STORAGE_KEY);
		state.messages = [
			{
				role: "assistant",
				content: "เริ่มแชตใหม่แล้วค่ะ ต้องการตรวจข้อมูลหรือเตรียมออเดอร์อะไรคะ?",
				suggestions: ["ค้นหาสินค้า", "ดู Sales Order ล่าสุด", "สรุป pipeline"],
			},
		];
		document.getElementById("nextgen-chat-history").hidden = true;
		render();
	}

	async function send(text) {
		const input = document.getElementById("nextgen-chat-input");
		const value = String(text ?? input.value).trim();
		if (!value || state.turnId) return;
		state.lastInput = value;
		input.value = "";
		state.messages.push({ role: "user", content: value });
		const pending = { role: "assistant", content: "", pending: true, action: null };
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
			});
			state.turnId = result.turn_id;
			state.sessionId = result.session_id;
			window.localStorage.setItem(STORAGE_KEY, state.sessionId);
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

	function actionCard(action) {
		if (!action?.preview) return "";
		const preview = action.preview;
		const items = (preview.items || [])
			.map(
				(item) => `<tr><td>${escapeHtml(item.item_code)}</td><td>${escapeHtml(item.qty)} ${escapeHtml(item.uom)}</td><td>${Number(item.amount || 0).toLocaleString("th-TH")}</td></tr>`,
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
			<div class="ng-action-total">รวม ${Number(preview.total || 0).toLocaleString("th-TH", { minimumFractionDigits: 2 })} THB</div>
			${warnings ? `<ul class="ng-action-warnings">${warnings}</ul>` : ""}
			<div class="ng-action-buttons" ${status !== "Pending" ? "hidden" : ""}>
				<button data-confirm-action="${escapeHtml(action.action_id)}">ยืนยันและดำเนินการ</button>
				<button class="secondary" data-cancel-action="${escapeHtml(action.action_id)}">ยกเลิก</button>
			</div>
			<div class="ng-action-result">${status !== "Pending" ? escapeHtml(status) : ""}${resultType && resultName ? ` · <button class="ng-doc-link" data-document-type="${escapeHtml(resultType)}" data-document-name="${escapeHtml(resultName)}">${escapeHtml(resultName)}</button>` : ""}</div>
		</section>`;
	}

	function render() {
		const container = document.getElementById("nextgen-chat-messages");
		if (!container) return;
		container.innerHTML = state.messages
			.map((message, index) => {
				const suggestions = (message.suggestions || [])
					.map((value) => `<button data-suggestion="${escapeHtml(value)}">${escapeHtml(value)}</button>`)
					.join("");
				const documentLink = message.href ? ` <button class="ng-doc-link" data-document-type="${escapeHtml(message.documentType)}" data-document-name="${escapeHtml(message.documentName)}">เปิดเอกสาร</button>` : "";
				return `<div class="ng-message ng-${message.role} ${message.error ? "ng-error" : ""}" data-index="${index}">
					<div class="ng-bubble">${message.pending && !message.content ? '<span class="ng-thinking">กำลังคิด</span>' : renderText(message.content)}${documentLink}</div>
					${message.action ? actionCard(message.action) : ""}
					${suggestions ? `<div class="ng-suggestions">${suggestions}</div>` : ""}
					${message.error ? '<button data-retry="1" class="ng-retry">ลองอีกครั้ง</button>' : ""}
				</div>`;
			})
			.join("");
		container.scrollTop = container.scrollHeight;
	}

	async function handleMessageClick(event) {
		const suggestion = event.target.closest("[data-suggestion]");
		if (suggestion) return send(suggestion.dataset.suggestion);
		if (event.target.closest("[data-retry]")) return send(state.lastInput);
		const confirm = event.target.closest("[data-confirm-action]");
		if (confirm) return confirmAction(confirm.dataset.confirmAction);
		const cancel = event.target.closest("[data-cancel-action]");
		if (cancel) return cancelAction(cancel.dataset.cancelAction);
		const link = event.target.closest("[data-document-type]");
		if (link) frappe.set_route("Form", link.dataset.documentType, link.dataset.documentName);
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
			const label = result.high_confidence ? "สร้างและจองสต๊อกสำเร็จ" : "ส่งเข้าคิวตรวจสอบแล้ว";
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

	async function toggleHistory() {
		const panel = document.getElementById("nextgen-chat-history");
		panel.hidden = !panel.hidden;
		if (!panel.hidden) {
			panel.innerHTML = '<div class="ng-history-loading">กำลังโหลด...</div>';
			try {
				const sessions = await call("list_sessions");
				panel.innerHTML = sessions.length
					? sessions.map((session) => `<button data-session="${escapeHtml(session.name)}"><strong>${escapeHtml(session.title)}</strong><small>${escapeHtml(session.last_activity_at)}</small></button>`).join("")
					: '<div class="ng-history-loading">ยังไม่มีประวัติ</div>';
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
			state.sessionId = sessionId;
			window.localStorage.setItem(STORAGE_KEY, sessionId);
			state.actions.clear();
			for (const action of data.actions || []) {
				state.actions.set(action.name, { ...action, action_id: action.name });
			}
			state.messages = (data.messages || []).map((message) => ({
				role: message.role,
				content: message.content,
				action: message.action ? state.actions.get(message.action) : null,
			}));
			document.getElementById("nextgen-chat-history").hidden = true;
			render();
			if (notify) frappe.show_alert({ message: "เปิดประวัติแชตแล้ว", indicator: "blue" });
		} catch {
			if (state.sessionId === sessionId) window.localStorage.removeItem(STORAGE_KEY);
		}
	}

	async function bootstrap() {
		if (frappe.session.user === "Guest") return;
		try {
			const status = await call("get_status");
			if (!status?.enabled) return;
			mount();
			frappe.realtime.on("nextgen_staff_chat", onRealtime);
		} catch (error) {
			console.warn("AI Sales Copilot is unavailable", error);
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
