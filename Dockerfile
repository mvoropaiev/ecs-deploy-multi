# base image
FROM python:3-alpine

# requirements
RUN set -ex \
    && pip install --no-cache-dir \
        boto3==1.4.4

# ecs deployer script
COPY ecs_deployer.py /usr/local/bin/ecs_deployer.py

# entrypoint / cmd
ENTRYPOINT ["ecs_deployer.py"]
CMD ["--help"]
