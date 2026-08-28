FROM maven:3.9.11-eclipse-temurin-21-alpine AS build
WORKDIR /build
COPY services/usl-sign-dss/pom.xml .
RUN --mount=type=cache,target=/root/.m2 mvn -B dependency:go-offline
COPY services/usl-sign-dss/src src
RUN --mount=type=cache,target=/root/.m2 mvn -B verify

FROM eclipse-temurin:21-jre-alpine AS verapdf
RUN apk add --no-cache bash curl unzip
WORKDIR /build
COPY services/usl-sign-dss/verapdf-auto-install.xml /build/auto-install.xml
RUN curl -fsSLo verapdf.zip https://software.verapdf.org/rel/1.30/verapdf-greenfield-1.30.2-installer.zip \
    && echo "6cc6341cb1af644044054b81f00a6590a7918abb18f762243de115258bcad838  verapdf.zip" | sha256sum -c - \
    && unzip -q verapdf.zip \
    && bash ./verapdf-greenfield-1.30.2/verapdf-install /build/auto-install.xml \
    && rm -rf /opt/verapdf/corpus /opt/verapdf/doc /opt/verapdf/plugins

FROM eclipse-temurin:21-jre-alpine
COPY services/usl-sign-dss/pyhanko-requirements.txt /tmp/pyhanko-requirements.txt
RUN apk add --no-cache py3-pip python3 \
    && python3 -m venv /opt/pyhanko \
    && /opt/pyhanko/bin/pip install --no-cache-dir --requirement /tmp/pyhanko-requirements.txt \
    && rm /tmp/pyhanko-requirements.txt \
    && addgroup -S dss \
    && adduser -S -G dss -h /app dss
WORKDIR /app
COPY --from=build /build/target/usl-sign-dss-1.0.0.jar /app/service.jar
COPY --from=verapdf /opt/verapdf /opt/verapdf
COPY addons/web/static/fonts/sign/NotoSans-Reg.ttf /app/NotoSans-Regular.ttf
COPY services/usl-sign-dss/pyhanko-validate.py /app/pyhanko-validate.py
COPY services/usl-sign-dss/pyhanko-fill-fields.py /app/pyhanko-fill-fields.py
COPY services/usl-sign-dss/pyhanko-normalize-xref.py /app/pyhanko-normalize-xref.py
USER dss
EXPOSE 8443
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=70", "-Djava.io.tmpdir=/tmp", "-jar", "/app/service.jar"]
