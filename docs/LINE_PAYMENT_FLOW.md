# LINE payment-first order flow

> **Document status:** Current LINE payment and order-flow capability. This behavior is a required regression contract in the [canonical acceptance tests](AI_HANDOFF/ACCEPTANCE_TESTS.md).

## Lifecycle

1. A customer sends Thai order text to the LINE Official Account.
2. Order Intake extracts the customer and items from the ERPNext catalog.
3. High-confidence orders go to customer confirmation; low-confidence orders go to **Needs Review**.
4. When the customer replies `ยืนยัน`, ERPNext submits the Sales Order, reserves stock with a Pick List, and submits the Sales Invoice.
5. LINE sends the invoice PDF link and an amount-locked PromptPay QR.
6. The customer attaches a slip image in LINE. ERPNext downloads it directly from LINE and stores it as a private attachment on **AI Order Intake**.
7. Typhoon OCR extracts the amount, reference, date/time, sender and recipient. Every result goes to **Payment Review**; OCR never records payment.
8. A staff member compares the OCR result with bank activity and explicitly approves it. Only then does ERPNext create the Payment Entry.
9. After approval, ERPNext creates a draft Delivery Note, confirms payment to the customer, and sends the Delivery Note PDF to the configured delivery-team LINE user/group.
9. The delivery team uses **Complete Delivery** in ERPNext to submit the Delivery Note and notify the customer.

The Delivery Note remains a draft until physical delivery so ERPNext does not post a stock movement prematurely.

## ERPNext configuration

Open **Order Agent → Payment Settings** and set:

- **PromptPay ID**: a 10-digit phone, 13-digit national/tax ID, or 15-digit e-wallet ID.
- **PromptPay Account Name**: display/reference information for operators.
- **Public Base URL**: the stable production HTTPS origin, such as `https://erp.example.com`.
- **Delivery Team LINE User/Group ID**: the destination for delivery instructions.
- **Typhoon OCR Base URL**: normally `https://api.opentyphoon.ai/v1`.
- **Typhoon OCR API Key** and **Typhoon OCR Model**: use `typhoon-ocr`.
- **OCR Review Confidence Threshold**: only raises a staff warning; it never auto-approves.
- **Private Slip Retention (Days)**: defaults to 365 days for completed/rejected orders; structured review audit data remains.

The LINE bot must be a member of the configured delivery group. LINE Channel Settings also needs a valid long-lived Channel Access Token so ERPNext can download slip images and push messages.

## Verification boundary

Typhoon OCR reads the slip image. NextGen flags amount/payee mismatches, missing
references, low confidence and duplicate references. This is document
extraction, not proof that money settled at the bank. A staff member must check
bank activity and use **Approve Payment Slip**. Duplicate references cannot be
approved.

For an end-to-end test, create a controlled THB 1 invoice, decode/scan the QR
without authorizing first, verify recipient and amount in the bank app, complete
the transfer, upload the slip, and confirm that no Payment Entry exists until a
staff member approves it.

## Deployment

After committing and pushing these changes, deploy normally. The Docker rebuild installs the QR dependency and `bench migrate` creates the new settings and fields:

```bash
cd ~/nextgen-erp-prototype
./scripts/deploy.sh
```

Then configure Payment Settings before testing a new order. Existing completed orders are not rewritten.
