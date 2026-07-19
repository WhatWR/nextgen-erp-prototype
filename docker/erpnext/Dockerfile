ARG ERPNEXT_VERSION=v16.20.0
FROM frappe/erpnext:${ERPNEXT_VERSION}

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=frappe:frappe apps/nextgen_erp /home/frappe/frappe-bench/apps/nextgen_erp

USER frappe
WORKDIR /home/frappe/frappe-bench

# ERPNext Thailand localisation (Thai WHT, tax invoice, ใบวางบิล, PromptPay,
# address-from-tax-ID). Pinned to a main commit for reproducible builds. Pull a
# newer commit by bumping ERPNEXT_THAILAND_REF. required_apps = erpnext only
# (HRMS is optional on main), so no extra app is needed. Installed onto the site
# separately via `bench install-app erpnext_thailand`.
ARG ERPNEXT_THAILAND_REF=32dcf9283ab0247ed9a4d6998641e4d60e42d0fd
RUN git clone https://github.com/ecosoft-frappe/erpnext_thailand apps/erpnext_thailand \
    && git -C apps/erpnext_thailand checkout "${ERPNEXT_THAILAND_REF}" \
    && ./env/bin/pip install --no-cache-dir --editable apps/erpnext_thailand

RUN ./env/bin/pip install --no-cache-dir --editable apps/nextgen_erp \
    && bench build --app nextgen_erp \
    && bench build --app erpnext_thailand \
    && ln -sfn /home/frappe/frappe-bench/apps/nextgen_erp/nextgen_erp/public \
        /home/frappe/frappe-bench/assets/nextgen_erp \
    && ln -sfn /home/frappe/frappe-bench/apps/erpnext_thailand/erpnext_thailand/public \
        /home/frappe/frappe-bench/assets/erpnext_thailand
