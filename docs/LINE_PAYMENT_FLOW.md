# LINE payment-first order flow

## Lifecycle

1. A customer sends Thai order text to the LINE Official Account.
2. Order Intake extracts the customer and items from the ERPNext catalog.
3. High-confidence orders go to customer confirmation; low-confidence orders go to **Needs Review**.
4. When the customer replies `ยืนยัน`, ERPNext submits the Sales Order, reserves stock with a Pick List, and submits the Sales Invoice.
5. LINE sends the invoice PDF link and an amount-locked PromptPay QR.
6. The customer attaches a slip image in LINE. ERPNext downloads it directly from LINE and stores it as a private attachment on **AI Order Intake**.
7. A configured slip verifier receives the image and expected invoice amount. High-confidence, amount-matching results create and submit the Payment Entry automatically. Every other result goes to **Payment Review**.
8. After payment, ERPNext creates a draft Delivery Note, confirms payment to the customer, and sends the Delivery Note PDF to the configured delivery-team LINE user/group.
9. The delivery team uses **Complete Delivery** in ERPNext to submit the Delivery Note and notify the customer.

The Delivery Note remains a draft until physical delivery so ERPNext does not post a stock movement prematurely.

## ERPNext configuration

Open **Order Agent → Payment Settings** and set:

- **PromptPay ID**: a 10-digit phone, 13-digit national/tax ID, or 15-digit e-wallet ID.
- **PromptPay Account Name**: display/reference information for operators.
- **Delivery Team LINE User/Group ID**: the destination for delivery instructions.
- **AI Slip Verification URL** and API key: optional but required for automatic slip approval.
- **Slip Auto-approval Confidence**: defaults to `0.95`.

The LINE bot must be a member of the configured delivery group. LINE Channel Settings also needs a valid long-lived Channel Access Token so ERPNext can download slip images and push messages.

## Slip verifier contract

NextGen sends an authenticated JSON request:

```json
{
  "image_base64": "...",
  "expected_amount": 996.0,
  "currency": "THB",
  "invoice": "ACC-SINV-2026-00001",
  "customer": "ร้านเจริญพาณิชย์"
}
```

The verifier returns:

```json
{
  "verified": true,
  "confidence": 0.99,
  "amount": 996.0,
  "reference_no": "BANK-TRANSACTION-ID",
  "reason": null
}
```

Automatic approval requires `verified=true`, confidence at or above the configured threshold, and an exact amount match within THB 0.01. A missing verifier never creates a Payment Entry automatically.

## Deployment

After committing and pushing these changes, deploy normally. The Docker rebuild installs the QR dependency and `bench migrate` creates the new settings and fields:

```bash
cd ~/nextgen-erp-prototype
./scripts/deploy.sh
```

Then configure Payment Settings before testing a new order. Existing completed orders are not rewritten.
