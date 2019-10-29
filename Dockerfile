FROM lambci/lambda:build-python3.7

WORKDIR /var/task
ENV WORKDIR /var/task

# Make the dir and to install all packages into packages/
COPY requirements.txt "$WORKDIR"
RUN mkdir -p deploy/purerackdiagram && \
    pip install -r requirements.txt -t deploy/ && \
    rm -rf deploy/*dist-info

RUN mkdir -p "$WORKDIR/deploy/purerackdiagram/png"
COPY lambdaentry.py "$WORKDIR/deploy/"
COPY purerackdiagram/*.py "$WORKDIR/deploy/purerackdiagram/"
COPY purerackdiagram/*.json "$WORKDIR/deploy/purerackdiagram/"
COPY purerackdiagram/*.ttf "$WORKDIR/deploy/purerackdiagram/"
COPY purerackdiagram/png/*.png "$WORKDIR/deploy/purerackdiagram/png/"

#pre-compile.pyc
RUN python -m compileall .  

# Compress all source codes.
RUN cd deploy && zip -r9 $WORKDIR/lambda.zip .

ENTRYPOINT [ "/bin/bash" ]