(ns io.github.getcolors.rybbit.tools-test
  (:require [clojure.string :as str]
            [clojure.test :refer [deftest is]]
            [io.github.getcolors.rybbit.tools :as tools]
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
