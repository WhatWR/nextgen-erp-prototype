# Thailand launch gates

The prototype is an operational overlay. Before it becomes a legal system of
record, obtain written validation and test evidence for each gate below.

## Pilot gate

- One food/FMCG wholesale workflow and two or three design partners.
- Shadow-mode evaluation on at least 100 real orders per partner.
- Field accuracy, straight-through rate, manual touches, processing time,
  duplicate/missed order rate and wrong-SKU rate measured before and after.
- PDPA role split, processing agreement, retention policy and subprocessor list.
- Authenticated staff roles, encrypted secrets, backup restoration and incident drill.

## Accounting and tax gate

- Thai CPA-approved chart and TFRS-for-NPAEs mapping.
- Effective-dated VAT configuration; never hard-code the temporary 7% rate.
- Thai tax ID and five-digit branch treatment.
- VAT/WHT books, P.P.30 and P.N.D.3/P.N.D.53 scenarios.
- Debit/credit note, reversal, closing lock and audit export tests.
- DBD e-Filing/XBRL mapping versioned by effective date.

## e-Tax and payments gate

- Use an approved e-Tax provider for signed XML, delivery acknowledgement and corrections.
- Confirm transfers with a bank-supported source; a slip image and OCR are not payment proof.
- Keep funds moving directly between buyer and merchant during the MVP.
- Obtain payments counsel review before initiating or holding customer funds.

## Legal and licensing gate

- Open-source counsel review of GPL boundaries and browser distribution.
- Separate product name and trademark review.
- Commercial/dual-licence discussion with AvanSaber if proprietary distribution is planned.
