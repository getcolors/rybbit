(ns io.github.getcolors.rybbit.tools-test
  (:require [clojure.string :as str]
            [clojure.test :refer [deftest is]]
            [io.github.getcolors.rybbit.tools :as tools]
            [io.github.getcolors.rybbit.validate :as validate]
            [io.github.getcolors.rybbit.validate-test :refer [fixture]]))

(deftest infrastructure-discovers-default-vpc
  (let [data (tools/infrastructure-data (fixture))]
    (is (= ["0.0.0.0/0" "::/0"] (tools/cidrs data :digitalocean-http-sources)))))

(deftest dns-is-apex-and-proxied
  (let [json (tools/dns-json (tools/dns-data (assoc (fixture) :ip "192.0.2.10")))]
    (is (str/includes? json "rybbit.example.com"))
    (is (str/includes? json "192.0.2.10"))
    ;; Assert the value, not the key: "proxied" appears in the rendered record
    ;; either way, so a bare includes? check passes on an unproxied record and
    ;; would not have caught the default being false.
    (is (str/includes? json "\"proxied\" : true"))))

(deftest dns-proxying-defaults-on-and-can-be-declined
  (is (true? (:cloudflare-proxied (tools/dns-data (fixture)))))
  (is (false? (:cloudflare-proxied
               (tools/dns-data (assoc (fixture) :cloudflare-proxied false)))))
  (is (str/includes? (tools/dns-json
                      (tools/dns-data (assoc (fixture) :ip "192.0.2.10"
                                             :cloudflare-proxied false)))
                     "\"proxied\" : false")))

(deftest inventory-keeps-one-private-target
  (let [inventory (tools/inventory (assoc (fixture) :ip "192.0.2.10"))]
    (is (str/includes? inventory "192.0.2.10"))
    (is (str/includes? inventory "rybbit-fixture"))))

(deftest ingestion-is-judged-by-the-stored-row-not-the-status
  (is (= :ingested (tools/ingestion-verdict "200" 4 5)))
  ;; The failure this gate exists for: the endpoint accepts and nothing lands.
  (is (= :dropped (tools/ingestion-verdict "200" 4 4)))
  (is (= :dropped (tools/ingestion-verdict "202" 4 nil)))
  (is (= :rejected (tools/ingestion-verdict "400" 4 4)))
  (is (= :unreachable (tools/ingestion-verdict nil 4 4))))

(deftest backup-must-be-fresh-and-non-empty
  (let [since (java.time.Instant/parse "2026-08-17T02:30:00Z")
        entry (fn [size mod-time] {:Size size :ModTime mod-time})]
    (is (tools/fresh-backup? [(entry 1024 "2026-08-17T02:30:05Z")] since))
    (is (tools/fresh-backup? [(entry 1024 "2026-08-17T04:30:05+02:00")] since))
    (is (not (tools/fresh-backup? [(entry 1024 "2026-08-16T02:30:05Z")] since)))
    (is (not (tools/fresh-backup? [(entry 0 "2026-08-17T02:30:05Z")] since)))
    (is (not (tools/fresh-backup? [] since)))
    (is (not (tools/fresh-backup? nil since)))))

(deftest clickhouse-backup-is-native-and-has-no-torn-fallback
  ;; A hot tar of the data directory races running merges: parts vanish
  ;; mid-read, tar exits non-zero and set -e aborts before the upload, which is
  ;; how this deployment ran for hours with nothing reaching R2.
  (let [backup (slurp "src/resources/io/github/getcolors/rybbit/tools/ansible/backup")]
    (is (str/includes? backup "BACKUP DATABASE"))
    (is (str/includes? backup "/var/lib/clickhouse/backups/"))
    (is (not (re-find #"\|\|\s*\{?\s*\n?\s*tar -czf" backup)))))

(def backup-script
  (delay (slurp "src/resources/io/github/getcolors/rybbit/tools/ansible/backup")))

(deftest backup-proves-it-restores-and-prunes-the-bucket
  (is (str/includes? @backup-script "CREATE DATABASE"))
  (is (str/includes? @backup-script "information_schema.tables"))
  (is (str/includes? @backup-script "rclone delete --min-age"))
  (let [restore (str/index-of @backup-script "restore check restored no tables")
        upload (str/index-of @backup-script "rclone copyto")]
    (is (< restore upload))))

(deftest signup-is-desired-state
  ;; Open registration on a public analytics instance should be a decision in
  ;; colors.yml, not a constant in the playbook.
  (let [playbook (slurp "src/resources/io/github/getcolors/rybbit/tools/ansible/main.yml")]
    (is (not (str/includes? playbook "DISABLE_SIGNUP=false")))
    (is (str/includes? playbook "DISABLE_SIGNUP=<{ rybbit-disable-signup }>"))))

(deftest images-are-pinned-not-floating
  ;; A moving tag is how the PostHog arm ended up running an application and a
  ;; plugin server built from different commits, querying a column that did not
  ;; exist. Digests cannot move under a deployment.
  (let [fixture (slurp "test/fixtures/colors.yml")]
    (is (not (re-find #"image:\s*\S+:(latest|master)\s*$" fixture)))
    (is (re-find #"rybbit-backend-image: \S+@sha256:[0-9a-f]{64}" fixture))
    (is (re-find #"rybbit-client-image: \S+@sha256:[0-9a-f]{64}" fixture))))

(deftest validation-accepts-a-digest-pin
  (is (= [] (validate/state-errors (fixture))))
  (is (seq (validate/state-errors (fixture :rybbit-backend-image "no-tag-at-all")))))

(deftest track-payload-uses-the-api-discriminator
  ;; Rybbit validates a `type` discriminator and rejects the request outright
  ;; without it; `name` produced a 400 that only surfaced once a site existed.
  (let [src (slurp "src/clj/io/github/getcolors/rybbit/tools.clj")]
    (is (str/includes? src ":type \"pageview\""))
    (is (str/includes? src ":pathname"))
    (is (not (str/includes? src ":name \"pageview\"")))))

(deftest acceptance-uses-a-throwaway-site
  ;; Sending to whichever site was first wrote a synthetic pageview into the
  ;; operator's real analytics on every converge.
  (let [src (slurp "src/clj/io/github/getcolors/rybbit/tools.clj")]
    (is (str/includes? src "acceptance-site-id"))
    (is (str/includes? src "rybbit-acceptance-site-domain"))
    ;; No falling back to an arbitrary existing site.
    (is (not (re-find #"select site_id from sites limit 1" src)))
    ;; Created only when absent, so a converge is idempotent.
    (is (str/includes? src "where not exists"))))

(deftest site-id-is-taken-from-the-last-line
  ;; psql prints "INSERT 0 1" before the selected id; the whole output is not
  ;; a site id.
  (let [src (slurp "src/clj/io/github/getcolors/rybbit/tools.clj")]
    (is (str/includes? src "str/split-lines"))
    (is (str/includes? src "re-matches #\"\\d+\""))))

(deftest a-missing-compute-output-fails-loudly
  ;; The documentation address belongs to build and dry-run. Merging it into a
  ;; real converge would point Ansible at TEST-NET instead of failing.
  (is (= "1.2.3.4" (:ip (tools/resolved-compute {} {:ip "192.0.2.10"} {:ip "1.2.3.4"}))))
  (is (= 1 (:green/exit (tools/resolved-compute {} {:ip "192.0.2.10"} nil))))
  (is (= 1 (:green/exit (tools/resolved-compute {} {:ip "192.0.2.10"} {}))))
  (is (nil? (:green/exit (tools/resolved-compute {} {:ip "192.0.2.10"} {:ip "5.6.7.8"})))))

(deftest signup-policy-is-reapplied-on-every-converge
  ;; stack.env is written once to keep its generated secrets, which also froze
  ;; the signup policy: changing the key afterwards silently did nothing.
  (let [playbook (slurp "src/resources/io/github/getcolors/rybbit/tools/ansible/main.yml")]
    (is (str/includes? playbook "lineinfile"))
    (is (str/includes? playbook "DISABLE_SIGNUP=<{ rybbit-disable-signup }>"))
    ;; An env_file is read when a container is created, not while it runs.
    (is (str/includes? playbook "--force-recreate backend client"))))

(def caddyfile
  (delay (slurp "src/resources/io/github/getcolors/rybbit/tools/ansible/Caddyfile")))

(def compose
  (delay (slurp "src/resources/io/github/getcolors/rybbit/tools/ansible/compose.yml")))

(def playbook
  (delay (slurp "src/resources/io/github/getcolors/rybbit/tools/ansible/main.yml")))

(deftest caddy-access-logging-is-on-and-bounded
  ;; Access logging is off by default in Caddy, so a successful request left no
  ;; trace and ingestion had no request-level evidence to debug from.
  (is (str/includes? @caddyfile "log {"))
  (is (str/includes? @caddyfile "output stdout"))
  ;; On, but bounded: json-file never rotates on its own and this endpoint
  ;; writes a line per request.
  (is (str/includes? @compose "max-size"))
  (is (str/includes? @compose "max-file")))

(deftest caddy-reload-is-convergent-not-change-triggered
  ;; The Caddyfile is a single-file bind mount, so copy-by-rename leaves the
  ;; container on the old inode and `up -d` will not recreate an unchanged
  ;; service: the host file looked right while Caddy served the old config.
  (is (str/includes? @playbook "--force-recreate caddy"))
  (is (str/includes? @playbook "sha256sum /etc/caddy/Caddyfile"))
  ;; And it must run once the stack is up, or it recreates against a compose
  ;; file that has not been rendered yet.
  (let [converge (str/index-of @playbook "Start Rybbit stack")
        reload (str/index-of @playbook "--force-recreate caddy")
        health (str/index-of @playbook "Wait for backend health endpoint")]
    (is (< converge reload health))))

(deftest access-log-records-the-visitor-not-the-proxy
  ;; Behind the Cloudflare proxy every connection arrives from an edge address,
  ;; so without trusted_proxies Caddy attributes each request to Cloudflare and
  ;; the access log answers "who sent this?" with the proxy. Verified against a
  ;; live deployment: the arm with this block logged the real client address
  ;; and the arm without it logged 162.158.x.
  (is (str/includes? @caddyfile "trusted_proxies static"))
  (is (str/includes? @caddyfile "162.158.0.0/15"))
  (is (str/includes? @caddyfile "2400:cb00::/32")))
