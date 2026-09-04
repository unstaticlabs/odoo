FROM golang:1.25-bookworm@sha256:3b4a11519ad929d1e1d261a12cff056f0c85b735253d7d861346b9c6f8b36437 AS build
ARG SMOKESCREEN_COMMIT=d4da883a671475551d78da812db547341c8fe6c3
ADD https://codeload.github.com/stripe/smokescreen/tar.gz/d4da883a671475551d78da812db547341c8fe6c3 /tmp/smokescreen.tar.gz
RUN echo "04ff26a2675afced7a6422748a7a680782a4050eb367d10bf511f9c879f2f32a  /tmp/smokescreen.tar.gz" | sha256sum --check \
    && mkdir /src \
    && tar --extract --gzip --file /tmp/smokescreen.tar.gz --directory /src --strip-components=1
COPY services/usl-receipt-egress/port-443-only.patch /tmp/port-443-only.patch
RUN cd /src \
    && git apply --unidiff-zero /tmp/port-443-only.patch \
    && CGO_ENABLED=0 go build -mod=vendor -trimpath -ldflags='-s -w' -o /smokescreen .

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab
COPY --from=build /smokescreen /usr/local/bin/smokescreen
USER nonroot:nonroot
ENTRYPOINT ["/usr/local/bin/smokescreen", "--listen-ip", "0.0.0.0", "--listen-port", "4750", "--timeout", "10s", "--dns-timeout", "5s", "--max-concurrent-requests", "12", "--max-request-rate", "20", "--max-request-burst", "30", "--max-concurrent-connect-tunnels", "8", "--additional-error-message-on-deny", "Receipt egress policy denied the destination"]
