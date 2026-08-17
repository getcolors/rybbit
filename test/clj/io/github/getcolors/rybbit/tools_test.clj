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
  (let [json (tools/dns-json (assoc (fixture) :ip "192.0.2.10"))]
    (is (str/includes? json "rybbit.example.com"))
    (is (str/includes? json "192.0.2.10"))
    (is (str/includes? json "proxied"))))

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
