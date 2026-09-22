FROM postgres:16-alpine
WORKDIR /maintenance
COPY maintenance/test_mobile_clear/reset.sql /maintenance/reset.sql
COPY maintenance/test_mobile_clear/run.sh /maintenance/run.sh
ENTRYPOINT ["/bin/sh", "/maintenance/run.sh"]
