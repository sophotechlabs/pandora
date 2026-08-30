// Command pandora-wrap runs a command, checks in around it, and reports a failure
// as an issue.
//
// One static binary in an initContainer turns backups, migrations, Ansible runs
// and CI steps into grouped issues with no application change. It pairs with the
// cron endpoint: a check-in on the way in, a check-in and an occurrence on the
// way out.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"strings"
	"syscall"
	"time"
)

const (
	defaultTimeout = 30 * time.Second
	outputLimit    = 16 * 1024
	exitUsage      = 2
)

type options struct {
	dsn     string
	monitor string
	env     string
	release string
	timeout time.Duration
	quiet   bool
}

type dsn struct {
	base      string
	projectID string
	publicKey string
}

func main() {
	opts, args, err := parseArgs(os.Args[1:])
	if err != nil {
		fmt.Fprintln(os.Stderr, "pandora-wrap:", err)
		os.Exit(exitUsage)
	}

	parsed, err := parseDSN(opts.dsn)
	if err != nil {
		fmt.Fprintln(os.Stderr, "pandora-wrap:", err)
		os.Exit(exitUsage)
	}

	code, err := run(context.Background(), parsed, opts, args)
	if err != nil {
		fmt.Fprintln(os.Stderr, "pandora-wrap:", err)
	}
	os.Exit(code)
}

func parseArgs(argv []string) (options, []string, error) {
	set := flag.NewFlagSet("pandora-wrap", flag.ContinueOnError)
	set.SetOutput(io.Discard)
	opts := options{}
	set.StringVar(&opts.dsn, "dsn", os.Getenv("PANDORA_DSN"), "pandora DSN")
	set.StringVar(&opts.monitor, "monitor", "", "monitor slug (default: the command)")
	set.StringVar(&opts.env, "environment", os.Getenv("PANDORA_ENVIRONMENT"), "environment")
	set.StringVar(&opts.release, "release", os.Getenv("PANDORA_RELEASE"), "release")
	set.DurationVar(&opts.timeout, "timeout", defaultTimeout, "how long to wait on pandora")
	set.BoolVar(&opts.quiet, "quiet", false, "do not echo the command's output")

	if err := set.Parse(argv); err != nil {
		return opts, nil, err
	}
	args := set.Args()
	if len(args) == 0 {
		return opts, nil, errors.New("usage: pandora-wrap [flags] -- <command> [args...]")
	}
	if opts.dsn == "" {
		return opts, nil, errors.New("no DSN: pass -dsn or set PANDORA_DSN")
	}
	if opts.monitor == "" {
		opts.monitor = slug(args[0])
	}
	return opts, args, nil
}

func run(ctx context.Context, parsed dsn, opts options, args []string) (int, error) {
	client := &http.Client{Timeout: opts.timeout}
	checkIn(ctx, client, parsed, opts, "in_progress")

	started := time.Now()
	output, code, runErr := execute(ctx, args, opts.quiet)
	elapsed := time.Since(started)

	status := "ok"
	if code != 0 || runErr != nil {
		status = "error"
	}
	checkIn(ctx, client, parsed, opts, status)

	if status == "ok" {
		return code, nil
	}
	report(ctx, client, parsed, opts, args, output, code, elapsed)
	return code, nil
}

func execute(ctx context.Context, args []string, quiet bool) (string, int, error) {
	ctx, stop := signal.NotifyContext(ctx, os.Interrupt, syscall.SIGTERM)
	defer stop()

	buffer := &bytes.Buffer{}
	command := exec.CommandContext(ctx, args[0], args[1:]...)
	command.Stdin = os.Stdin
	if quiet {
		command.Stdout = buffer
		command.Stderr = buffer
	} else {
		command.Stdout = io.MultiWriter(os.Stdout, buffer)
		command.Stderr = io.MultiWriter(os.Stderr, buffer)
	}

	err := command.Run()
	code := command.ProcessState.ExitCode()
	if code < 0 {
		code = 1
	}
	return tail(buffer.String()), code, err
}

func checkIn(ctx context.Context, client *http.Client, parsed dsn, opts options, status string) {
	body, _ := json.Marshal(map[string]any{
		"status":      status,
		"environment": opts.env,
	})
	endpoint := fmt.Sprintf(
		"%s/api/%s/cron/%s/%s/",
		parsed.base, parsed.projectID, opts.monitor, parsed.publicKey,
	)
	post(ctx, client, endpoint, body)
}

func report(
	ctx context.Context,
	client *http.Client,
	parsed dsn,
	opts options,
	args []string,
	output string,
	code int,
	elapsed time.Duration,
) {
	title := fmt.Sprintf("%s exited %d", strings.Join(args, " "), code)
	payload := map[string]any{
		"level":    "error",
		"platform": "other",
		"logentry": map[string]any{"formatted": title},
		"exception": map[string]any{
			"values": []map[string]any{{
				"type":  "CommandFailed",
				"value": title,
			}},
		},
		"tags": map[string]string{
			"monitor":   opts.monitor,
			"exit_code": fmt.Sprint(code),
			"command":   args[0],
		},
		"extra": map[string]any{
			"output":  output,
			"seconds": elapsed.Seconds(),
			"argv":    args,
		},
	}
	if opts.env != "" {
		payload["environment"] = opts.env
	}
	if opts.release != "" {
		payload["release"] = opts.release
	}
	body, _ := json.Marshal(payload)
	endpoint := fmt.Sprintf(
		"%s/api/%s/store/?sentry_key=%s", parsed.base, parsed.projectID, parsed.publicKey,
	)
	post(ctx, client, endpoint, body)
}

func post(ctx context.Context, client *http.Client, endpoint string, body []byte) {
	request, err := http.NewRequestWithContext(
		ctx, http.MethodPost, endpoint, bytes.NewReader(body),
	)
	if err != nil {
		return
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := client.Do(request)
	if err != nil {
		fmt.Fprintln(os.Stderr, "pandora-wrap: could not reach pandora:", err)
		return
	}
	defer response.Body.Close()
	io.Copy(io.Discard, response.Body)
}

func parseDSN(raw string) (dsn, error) {
	parsed, err := url.Parse(raw)
	if err != nil {
		return dsn{}, fmt.Errorf("DSN is not a URL: %w", err)
	}
	if parsed.User == nil || parsed.User.Username() == "" {
		return dsn{}, errors.New("DSN carries no public key")
	}
	project := strings.Trim(parsed.Path, "/")
	if project == "" {
		return dsn{}, errors.New("DSN carries no project id")
	}
	base := fmt.Sprintf("%s://%s", parsed.Scheme, parsed.Host)
	return dsn{base: base, projectID: project, publicKey: parsed.User.Username()}, nil
}

func slug(text string) string {
	name := text
	if index := strings.LastIndex(name, "/"); index >= 0 {
		name = name[index+1:]
	}
	cleaned := strings.Map(func(r rune) rune {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9', r == '-':
			return r
		case r >= 'A' && r <= 'Z':
			return r + 32
		case r == '_' || r == ' ' || r == '.':
			return '-'
		default:
			return -1
		}
	}, name)
	if cleaned == "" {
		return "command"
	}
	return cleaned
}

func tail(text string) string {
	if len(text) <= outputLimit {
		return text
	}
	return text[len(text)-outputLimit:]
}
