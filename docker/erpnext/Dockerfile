ARG ERPNEXT_VERSION=v16.20.0
FROM frappe/erpnext:${ERPNEXT_VERSION}

USER root
COPY --chown=frappe:frappe apps/nextgen_erp /home/frappe/frappe-bench/apps/nextgen_erp

USER frappe
WORKDIR /home/frappe/frappe-bench
RUN ./env/bin/pip install --no-cache-dir --editable apps/nextgen_erp \
    && bench build --app nextgen_erp \
    && ln -sfn /home/frappe/frappe-bench/apps/nextgen_erp/nextgen_erp/public \
        /home/frappe/frappe-bench/assets/nextgen_erp
