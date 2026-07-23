ARG ERPNEXT_VERSION=v16.20.0
FROM frappe/erpnext:${ERPNEXT_VERSION}

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-core poppler-utils \
    && rm -rf /var/lib/apt/lists/*
USER frappe
WORKDIR /home/frappe/frappe-bench

# ERPNext Thailand localisation (Thai WHT, tax invoice, ใบวางบิล, PromptPay,
# address-from-tax-ID). Pinned to a main commit for reproducible builds. Pull a
# newer commit by bumping ERPNEXT_THAILAND_REF. required_apps = erpnext only
# (HRMS is optional on main), so no extra app is needed. Installed onto the site
# separately via `bench install-app erpnext_thailand`.
ARG ERPNEXT_THAILAND_REF=32dcf9283ab0247ed9a4d6998641e4d60e42d0fd
ARG PAYMENTS_REF=cca07d9f9392e2ea0e521c5975151db9e4b6c321
ARG WEBSHOP_REF=3afd734a495d5869f05a4f0ae36fc76330eb7734
RUN git clone --filter=blob:none https://github.com/ecosoft-frappe/erpnext_thailand apps/erpnext_thailand \
    && git -C apps/erpnext_thailand checkout "${ERPNEXT_THAILAND_REF}"
RUN git clone --filter=blob:none https://github.com/frappe/payments.git apps/payments \
    && git -C apps/payments checkout "${PAYMENTS_REF}"
RUN mkdir -p apps/webshop \
    && git -C apps/webshop init \
    && git -C apps/webshop remote add origin https://github.com/frappe/webshop.git \
    && git -C apps/webshop fetch --depth=1 origin "${WEBSHOP_REF}" \
    && git -C apps/webshop checkout --detach FETCH_HEAD
RUN ./env/bin/pip install --no-cache-dir --editable apps/erpnext_thailand \
    && ./env/bin/pip install --no-cache-dir --editable apps/payments \
    && ./env/bin/pip install --no-cache-dir --editable apps/webshop

COPY --chown=frappe:frappe apps/nextgen_erp /home/frappe/frappe-bench/apps/nextgen_erp
COPY --chown=frappe:frappe import.py /home/frappe/frappe-bench/import.py
COPY --chown=frappe:frappe data/imports /home/frappe/frappe-bench/imports

RUN ./env/bin/pip install --no-cache-dir --editable apps/nextgen_erp \
    && bench build --app nextgen_erp \
    && bench build --app erpnext_thailand \
    && bench build --app payments \
    && bench build --app webshop \
    && ln -sfn /home/frappe/frappe-bench/apps/nextgen_erp/nextgen_erp/public \
        /home/frappe/frappe-bench/assets/nextgen_erp \
    && ln -sfn /home/frappe/frappe-bench/apps/erpnext_thailand/erpnext_thailand/public \
        /home/frappe/frappe-bench/assets/erpnext_thailand \
    && ln -sfn /home/frappe/frappe-bench/apps/payments/payments/public \
        /home/frappe/frappe-bench/assets/payments \
    && ln -sfn /home/frappe/frappe-bench/apps/webshop/webshop/public \
        /home/frappe/frappe-bench/assets/webshop
