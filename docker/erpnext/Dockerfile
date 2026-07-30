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
ARG CRM_REF=bf1b7f07ac01b6ac435f25db7ccef6b52807720e
ARG HRMS_REF=a29cc8875aab1f22707b011568f5f1f190861fc8
RUN git clone --filter=blob:none https://github.com/ecosoft-frappe/erpnext_thailand apps/erpnext_thailand \
    && git -C apps/erpnext_thailand checkout "${ERPNEXT_THAILAND_REF}"
RUN git clone --filter=blob:none https://github.com/frappe/payments.git apps/payments \
    && git -C apps/payments checkout "${PAYMENTS_REF}"
RUN mkdir -p apps/webshop \
    && git -C apps/webshop init \
    && git -C apps/webshop remote add origin https://github.com/frappe/webshop.git \
    && git -C apps/webshop fetch --depth=1 origin "${WEBSHOP_REF}" \
    && git -C apps/webshop checkout --detach FETCH_HEAD
RUN git clone --filter=blob:none --branch main https://github.com/frappe/crm.git apps/crm \
    && git -C apps/crm checkout "${CRM_REF}" \
    && git -C apps/crm submodule update --init --recursive --depth=1
RUN git clone --filter=blob:none --branch version-16 https://github.com/frappe/hrms.git apps/hrms \
    && git -C apps/hrms checkout "${HRMS_REF}" \
    && git -C apps/hrms submodule update --init --recursive --depth=1
RUN ./env/bin/pip install --no-cache-dir --editable apps/erpnext_thailand \
    && ./env/bin/pip install --no-cache-dir --editable apps/payments \
    && ./env/bin/pip install --no-cache-dir --editable apps/webshop \
    && ./env/bin/pip install --no-cache-dir --editable apps/crm \
    && ./env/bin/pip install --no-cache-dir --editable apps/hrms

RUN yarn --cwd apps/crm install --frozen-lockfile \
    && yarn --cwd apps/hrms install --frozen-lockfile

COPY --chown=frappe:frappe apps/nextgen_erp /home/frappe/frappe-bench/apps/nextgen_erp
COPY --chown=frappe:frappe import.py /home/frappe/frappe-bench/import.py
COPY --chown=frappe:frappe data/imports /home/frappe/frappe-bench/imports

RUN ./env/bin/pip install --no-cache-dir --editable apps/nextgen_erp \
    && bench build --app nextgen_erp \
    && bench build --app erpnext_thailand \
    && bench build --app payments \
    && bench build --app webshop \
    && bench build --app crm \
    && bench build --app hrms \
    && ln -sfn /home/frappe/frappe-bench/apps/nextgen_erp/nextgen_erp/public \
        /home/frappe/frappe-bench/assets/nextgen_erp \
    && ln -sfn /home/frappe/frappe-bench/apps/erpnext_thailand/erpnext_thailand/public \
        /home/frappe/frappe-bench/assets/erpnext_thailand \
    && ln -sfn /home/frappe/frappe-bench/apps/payments/payments/public \
        /home/frappe/frappe-bench/assets/payments \
    && ln -sfn /home/frappe/frappe-bench/apps/webshop/webshop/public \
        /home/frappe/frappe-bench/assets/webshop \
    && ln -sfn /home/frappe/frappe-bench/apps/crm/crm/public \
        /home/frappe/frappe-bench/assets/crm \
    && ln -sfn /home/frappe/frappe-bench/apps/hrms/hrms/public \
        /home/frappe/frappe-bench/assets/hrms

# The server mounts `sites` from a persistent volume. Docker initializes a
# volume only once, so a volume created before Webshop was added never receives
# its bundles from later images. Keep an immutable image copy for the
# configurator to merge into the shared volume on every deployment.
RUN mkdir -p /home/frappe/frappe-bench/image-assets \
    && cp -a /home/frappe/frappe-bench/sites/assets/. \
        /home/frappe/frappe-bench/image-assets/
