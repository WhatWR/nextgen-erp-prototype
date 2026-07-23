(() => {
	const checkout = (event) => {
		const button = event.target.closest(".btn-place-order, .btn-request-for-quotation");
		if (!button || window.location.pathname !== "/cart") return;
		event.preventDefault();
		event.stopImmediatePropagation();
		button.disabled = true;
		button.textContent = __("กำลังยืนยันออเดอร์...");
		frappe.call({
			type: "POST",
			method: "nextgen_erp.webshop.checkout_line_cart",
			callback(response) {
				const order = response.message || {};
				if (response.exc || !order.name) {
					button.disabled = false;
					button.textContent = __("ยืนยันและสั่งซื้อ");
					return;
				}
				window.location.href =
					"/nextgen-checkout-success?order=" + encodeURIComponent(order.name);
			},
			error() {
				button.disabled = false;
				button.textContent = __("ยืนยันและสั่งซื้อ");
			},
		});
	};

	document.addEventListener("click", checkout, true);
	document.addEventListener("DOMContentLoaded", () => {
		document
			.querySelectorAll(".btn-place-order, .btn-request-for-quotation")
			.forEach((button) => {
				button.textContent = __("ยืนยันและสั่งซื้อ");
				button.classList.remove("btn-request-for-quotation");
				button.classList.add("btn-place-order");
			});
	});
})();
