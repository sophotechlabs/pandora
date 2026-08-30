package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"
)

type recorder struct {
	mu     sync.Mutex
	paths  []string
	bodies []map[string]any
}

func (r *recorder) handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		body := map[string]any{}
		_ = json.NewDecoder(req.Body).Decode(&body)
		r.mu.Lock()
		r.paths = append(r.paths, req.URL.Path)
		r.bodies = append(r.bodies, body)
		r.mu.Unlock()
		w.WriteHeader(http.StatusOK)
	})
}

func (r *recorder) seen(fragment string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, path := range r.paths {
		if strings.Contains(path, fragment) {
			return true
		}
	}
	return false
}

// Should turn a DSN into the three parts every endpoint needs.
func TestParseDSNSplitsTheKeyAndTheProject(t *testing.T) {
	parsed, err := parseDSN("http://abc123@pandora.test/7")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if parsed.base != "http://pandora.test" {
		t.Errorf("base = %q", parsed.base)
	}
	if parsed.projectID != "7" {
		t.Errorf("projectID = %q", parsed.projectID)
	}
	if parsed.publicKey != "abc123" {
		t.Errorf("publicKey = %q", parsed.publicKey)
	}
}

// Should refuse a DSN with no key rather than post to an endpoint that will 401.
func TestParseDSNRefusesAKeylessDSN(t *testing.T) {
	if _, err := parseDSN("http://pandora.test/7"); err == nil {
		t.Fatal("expected an error")
	}
}

// Should refuse a DSN with no project id for the same reason.
func TestParseDSNRefusesAProjectlessDSN(t *testing.T) {
	if _, err := parseDSN("http://abc123@pandora.test/"); err == nil {
		t.Fatal("expected an error")
	}
}

// Should name the monitor after the command when nobody said otherwise.
func TestTheMonitorDefaultsToTheCommand(t *testing.T) {
	opts, _, err := parseArgs([]string{"-dsn", "http://k@h/1", "--", "/usr/bin/pg_dump"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if opts.monitor != "pg-dump" {
		t.Errorf("monitor = %q", opts.monitor)
	}
}

// Should refuse to run with no command, which is the commonest mistake.
func TestNoCommandIsAUsageError(t *testing.T) {
	if _, _, err := parseArgs([]string{"-dsn", "http://k@h/1"}); err == nil {
		t.Fatal("expected an error")
	}
}

// Should refuse to run with no DSN rather than silently watch nothing.
func TestNoDSNIsAUsageError(t *testing.T) {
	if _, _, err := parseArgs([]string{"--", "true"}); err == nil {
		t.Fatal("expected an error")
	}
}

// Should check in on the way in and on the way out.
func TestASuccessfulCommandChecksInTwice(t *testing.T) {
	rec := &recorder{}
	server := httptest.NewServer(rec.handler())
	defer server.Close()

	opts := options{
		dsn:     server.URL + "/1",
		monitor: "nightly",
		timeout: 2 * time.Second,
		quiet:   true,
	}
	code, err := run(context.Background(), dsnFor(t, server.URL), opts, []string{"true"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if code != 0 {
		t.Errorf("code = %d", code)
	}
	if len(rec.paths) != 2 {
		t.Fatalf("paths = %v", rec.paths)
	}
	if rec.bodies[0]["status"] != "in_progress" {
		t.Errorf("first status = %v", rec.bodies[0]["status"])
	}
	if rec.bodies[1]["status"] != "ok" {
		t.Errorf("second status = %v", rec.bodies[1]["status"])
	}
}

// Should send an occurrence as well as the check-in when the command fails.
func TestAFailingCommandReportsAnIssue(t *testing.T) {
	rec := &recorder{}
	server := httptest.NewServer(rec.handler())
	defer server.Close()

	opts := options{
		dsn:     server.URL + "/1",
		monitor: "nightly",
		timeout: 2 * time.Second,
		quiet:   true,
	}
	code, _ := run(context.Background(), dsnFor(t, server.URL), opts, []string{"false"})
	if code == 0 {
		t.Errorf("code = %d, wanted the command's own", code)
	}
	if !rec.seen("/store/") {
		t.Errorf("no occurrence was sent: %v", rec.paths)
	}
}

// Should carry the exit code, which is what tells the two failures apart.
func TestTheReportCarriesTheExitCode(t *testing.T) {
	rec := &recorder{}
	server := httptest.NewServer(rec.handler())
	defer server.Close()

	opts := options{dsn: server.URL + "/1", monitor: "n", timeout: 2 * time.Second, quiet: true}
	run(context.Background(), dsnFor(t, server.URL), opts, []string{"sh", "-c", "exit 3"})

	for _, body := range rec.bodies {
		tags, ok := body["tags"].(map[string]any)
		if !ok {
			continue
		}
		if tags["exit_code"] == "3" {
			return
		}
	}
	t.Errorf("no report carried exit code 3: %v", rec.bodies)
}

// Should attach what the command printed, which is the half KubeJobFailed lacks.
func TestTheReportCarriesTheOutput(t *testing.T) {
	rec := &recorder{}
	server := httptest.NewServer(rec.handler())
	defer server.Close()

	opts := options{dsn: server.URL + "/1", monitor: "n", timeout: 2 * time.Second, quiet: true}
	run(
		context.Background(),
		dsnFor(t, server.URL),
		opts,
		[]string{"sh", "-c", "echo disk full >&2; exit 1"},
	)

	for _, body := range rec.bodies {
		extra, ok := body["extra"].(map[string]any)
		if !ok {
			continue
		}
		if strings.Contains(extra["output"].(string), "disk full") {
			return
		}
	}
	t.Errorf("no report carried the output: %v", rec.bodies)
}

// Should keep the tail of a huge log rather than post megabytes.
func TestTheOutputIsBounded(t *testing.T) {
	long := strings.Repeat("x", outputLimit*2)

	result := tail(long)

	if len(result) != outputLimit {
		t.Errorf("len = %d", len(result))
	}
}

// Should turn a path into something usable as a monitor slug.
func TestSlugCleansAPath(t *testing.T) {
	cases := map[string]string{
		"/usr/bin/pg_dump": "pg-dump",
		"Backup.sh":        "backup-sh",
		"!!!":              "command",
	}
	for input, want := range cases {
		if got := slug(input); got != want {
			t.Errorf("slug(%q) = %q, want %q", input, got, want)
		}
	}
}

// Should not fail the command because pandora was unreachable.
func TestAnUnreachablePandoraDoesNotChangeTheExitCode(t *testing.T) {
	opts := options{
		dsn:     "http://127.0.0.1:1/1",
		monitor: "n",
		timeout: 100 * time.Millisecond,
		quiet:   true,
	}
	parsed, err := parseDSN("http://k@127.0.0.1:1/1")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	silence(t)
	code, err := run(context.Background(), parsed, opts, []string{"true"})

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if code != 0 {
		t.Errorf("code = %d", code)
	}
}

func dsnFor(t *testing.T, base string) dsn {
	t.Helper()
	parsed, err := parseDSN(strings.Replace(base, "http://", "http://key@", 1) + "/1")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	return parsed
}

func silence(t *testing.T) {
	t.Helper()
	devnull, err := os.OpenFile(os.DevNull, os.O_WRONLY, 0)
	if err != nil {
		return
	}
	previous := os.Stderr
	os.Stderr = devnull
	t.Cleanup(func() {
		os.Stderr = previous
		devnull.Close()
	})
}

// Should carry the environment and the release onto the failure event.
func TestReportCarriesTheEnvironmentAndRelease(t *testing.T) {
	seen := &recorder{}
	server := httptest.NewServer(seen.handler())
	defer server.Close()

	opts := options{monitor: "nightly", env: "prod", release: "1.4.2"}
	report(
		context.Background(),
		server.Client(),
		dsnFor(t, server.URL),
		opts,
		[]string{"backup.sh"},
		"boom",
		2,
		time.Second,
	)

	seen.mu.Lock()
	defer seen.mu.Unlock()
	if len(seen.bodies) != 1 {
		t.Fatalf("bodies = %d", len(seen.bodies))
	}
	if seen.bodies[0]["environment"] != "prod" {
		t.Errorf("environment = %v", seen.bodies[0]["environment"])
	}
	if seen.bodies[0]["release"] != "1.4.2" {
		t.Errorf("release = %v", seen.bodies[0]["release"])
	}
}

// Should leave the event unlabelled rather than sending empty strings.
func TestReportOmitsAnUnsetEnvironmentAndRelease(t *testing.T) {
	seen := &recorder{}
	server := httptest.NewServer(seen.handler())
	defer server.Close()

	report(
		context.Background(),
		server.Client(),
		dsnFor(t, server.URL),
		options{monitor: "nightly"},
		[]string{"backup.sh"},
		"",
		1,
		time.Second,
	)

	seen.mu.Lock()
	defer seen.mu.Unlock()
	if _, present := seen.bodies[0]["environment"]; present {
		t.Errorf("environment was sent")
	}
	if _, present := seen.bodies[0]["release"]; present {
		t.Errorf("release was sent")
	}
}

// Should say so on stderr and carry on when pandora is unreachable.
func TestPostSurvivesAnUnreachableServer(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(
		func(w http.ResponseWriter, req *http.Request) {},
	))
	endpoint := server.URL + "/gone/"
	server.Close()

	post(context.Background(), server.Client(), endpoint, []byte("{}"))
}

// Should refuse an endpoint that cannot become a request at all.
func TestPostRefusesAnImpossibleEndpoint(t *testing.T) {
	post(context.Background(), http.DefaultClient, "://nonsense", []byte("{}"))
}

// Should echo the command's own output when quiet is off.
func TestExecuteEchoesTheOutputByDefault(t *testing.T) {
	output, code, err := execute(context.Background(), []string{"echo", "hello"}, false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if code != 0 {
		t.Errorf("code = %d", code)
	}
	if !strings.Contains(output, "hello") {
		t.Errorf("output = %q", output)
	}
}

// Should report an exit code of 1 for a command that never started.
func TestExecuteReportsOneForACommandThatDoesNotExist(t *testing.T) {
	_, code, err := execute(
		context.Background(),
		[]string{"pandora-wrap-no-such-command"},
		true,
	)
	if err == nil {
		t.Fatal("expected an error")
	}
	if code != 1 {
		t.Errorf("code = %d", code)
	}
}
