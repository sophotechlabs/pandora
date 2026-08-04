import os

from opentelemetry.sdk import resources

from pandora.web import telemetry

# configuration


def test_the_endpoint_is_read_and_trimmed(monkeypatch):
    """Should read the OTLP endpoint from the env and strip stray whitespace."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "  http://alloy:4318  ")

    result = telemetry.endpoint()
    expected = "http://alloy:4318"

    assert result == expected


def test_the_endpoint_is_empty_when_unset(monkeypatch):
    """Should report no endpoint when the env is absent."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    result = telemetry.endpoint()
    expected = ""

    assert result == expected


def test_the_instance_id_prefers_the_pod_name(monkeypatch):
    """Should identify a replica by pod name so traces separate per pod."""
    monkeypatch.setenv("POD_NAME", "pandora-web-abc")

    result = telemetry.instance_id()
    expected = f"pandora-web-abc:{os.getpid()}"

    assert result == expected


def test_the_instance_id_falls_back_to_the_hostname(monkeypatch):
    """Should still identify the process outside Kubernetes."""
    monkeypatch.delenv("POD_NAME", raising=False)
    monkeypatch.setattr(telemetry.socket, "gethostname", lambda: "laptop")

    result = telemetry.instance_id()
    expected = f"laptop:{os.getpid()}"

    assert result == expected


def test_nothing_is_installed_before_configure_runs():
    """Should report no provider until telemetry is wired."""
    result = telemetry.installed()

    assert result is False


# configure tests


def test_configure_is_a_noop_without_an_endpoint(monkeypatch, mocker):
    """Should skip instrumentation entirely when no collector is configured."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    install = mocker.patch.object(telemetry, "_install")

    result = telemetry.configure()

    assert result is False
    assert install.call_count == 0


def test_configure_is_a_noop_when_already_installed(monkeypatch, mocker):
    """Should not double-instrument when wsgi and asgi both boot in one process."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://alloy:4318")
    mocker.patch.object(telemetry, "installed", return_value=True)
    install = mocker.patch.object(telemetry, "_install")

    result = telemetry.configure()

    assert result is False
    assert install.call_count == 0


def test_configure_installs_once_when_enabled(monkeypatch, mocker):
    """Should install with a resource carrying the replica's instance id."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://alloy:4318")
    monkeypatch.setenv("POD_NAME", "pandora-reconcile-xyz")
    mocker.patch.object(telemetry, "installed", return_value=False)
    install = mocker.patch.object(telemetry, "_install")

    result = telemetry.configure()

    resource = install.call_args.args[0]
    assert result is True
    assert resource.attributes["service.instance.id"].startswith(
        "pandora-reconcile-xyz:"
    )


def test_install_wires_providers_and_instrumentors(mocker):
    """Should install trace and metric providers and the three instrumentors."""
    set_tracer_provider = mocker.patch.object(telemetry.trace, "set_tracer_provider")
    set_meter_provider = mocker.patch.object(telemetry.metrics, "set_meter_provider")
    mocker.patch.object(telemetry, "OTLPSpanExporter")
    mocker.patch.object(telemetry, "OTLPMetricExporter")
    instrumentors = [
        mocker.patch.object(telemetry, name)
        for name in (
            "DjangoInstrumentor",
            "RequestsInstrumentor",
            "PsycopgInstrumentor",
        )
    ]
    resource = resources.Resource.create({"service.instance.id": "pod:1"})

    telemetry._install(resource)

    result = [
        instrumentor.return_value.instrument.call_count
        for instrumentor in instrumentors
    ]
    expected = [1, 1, 1]
    assert result == expected
    assert set_tracer_provider.call_args.args[0].resource is resource
    assert isinstance(set_meter_provider.call_args.args[0], telemetry.MeterProvider)
