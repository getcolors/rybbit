(ns io.github.getcolors.rybbit.tools
  (:require [cheshire.core :as json]
            [clojure.string :as str]
            [clojure.walk :as walk]
            [green.ansible :as ansible]
            [green.cli :as green-cli]
            [green.process :as process]
            [green.scaffold :as sc]
            [green.tofu :as tofu]
            [green.workflow :as wf]
            [io.github.getcolors.rybbit.compute :as compute]
            [io.github.getcolors.rybbit.ssh :as ssh]
            [io.github.getcolors.rybbit.ssh-config :as ssh-config]
            [io.github.getcolors.rybbit.validate :as validate]))

(def infrastructure-tool "rybbit-infrastructure")
(def dns-tool "rybbit-dns")
(def ansible-tool "rybbit-ansible")
(def ansible-local-tool "rybbit-ansible-local")
(def root "io.github.getcolors.rybbit.tools")
(def template-opts sc/preserve-jinja-delimiters)
(defn tool-dir [opts tool] (green-cli/stage-dir opts tool {:default-profile "rybbit"}))
(defn template [path file] (keyword (str root "." path) file))
(defn spec [source target data] {:template source :target target :data data :opts template-opts})
(defn raw-spec [target content] (sc/content-spec target content))
(defn credential-env [opts & slots]
  (not-empty
   (into {} (keep (fn [[k env-var]]
                    (when-let [v (not-empty (str (get opts k)))] [env-var v])))
         (apply merge (map #(validate/tofu-env opts %) (conj (vec slots) :provider-backend))))))
(defn backend-credential-env [opts] (credential-env opts))

(defn fallback-params [opts]
 (when (and (#{:create :delete} (:green/event opts)) (not (:green/dry-run opts))) (throw (ex-info "compute node unavailable" {})))
 (compute/node (compute/planned opts)))
(def infrastructure-step compute/infrastructure-step)

(defn zone-id [zone] (format "${data.cloudflare_zone.zone.id}" zone))
(defn dns-data [opts]
  (let [host (str (:rybbit-host opts))
        zone (or (:cloudflare-zone opts)
                 (let [parts (str/split host #"\.")]
                   (if (> (count parts) 2)
                     (str/join "." (rest parts))
                     host)))]
    (assoc opts
           :ip (or (:ip opts) (:ip (fallback-params opts)))
           :cloudflare-zone zone
           ;; Proxied by default: an unproxied record publishes the droplet's
           ;; address, leaving the firewall as the only thing in front of the
           ;; origin. The Caddyfile already trusts Cloudflare's ranges, so
           ;; client addresses still come from X-Forwarded-For and geo and ASN
           ;; attribution are unaffected. Set the key to false to opt out --
           ;; note that doing so is also what keeps ssh to the host name
           ;; working, which a converge never needs but an operator may.
           :cloudflare-proxied (if (some? (:cloudflare-proxied opts))
                                 (:cloudflare-proxied opts)
                                 true))))

(defn dns-json [opts]
  (tofu/constructs-json
   [(tofu/construct :resource :cloudflare_dns_record :rybbit
                    {:zone_id (zone-id (:cloudflare-zone opts))
                     :name (:rybbit-host opts) :content (:ip opts) :type "A"
                     :proxied (boolean (:cloudflare-proxied opts)) :ttl 1})]))

(defn dns-step [opts]
  (let [dir (tool-dir opts dns-tool)
        data (dns-data opts)
        specs [(spec (template "dns" "main.tf") (str dir "/main.tf") data)
               (raw-spec (str dir "/record.tf.json") (dns-json data))]]
    (tofu/tofu-with-spec opts specs {:dir dir :env (credential-env opts :provider-dns)})))

;; ---------------------------------------------------------- ansible (local)

(defn ansible-local-data
  "Only what a `build` genuinely knows. The address, the user and the alias are
  run-time facts and reach the play as extra-vars instead, so the rendered
  playbook carries no IP and is identical on every workstation (SSH Config
  Standard §6)."
  [opts]
  (assoc opts
         :ssh-keygen (validate/keygen? opts) :ssh-identity-present (boolean (:ssh-private-key-path opts))
         :ssh-config-identity-file (ssh-config/identity-file opts)))

(defn ansible-local-specs [opts]
  (let [dir (tool-dir opts ansible-local-tool) data (ansible-local-data opts)]
    [(spec (template "ansible-local" "ansible.cfg") (str dir "/ansible.cfg") data)
     (spec (template "ansible-local" "inventory.ini") (str dir "/inventory.ini") data)
     (spec (template "ansible-local" "main.yml") (str dir "/main.yml") data)]))

(defn ansible-local-step
  "Write or remove the `~/.ssh/config` block. The same playbook serves both
  events; `block_state` is what distinguishes them."
  [opts]
  (let [dir (tool-dir opts ansible-local-tool)
        delete? (= :delete (:green/event opts))]
    (ansible/ansible-with-spec opts
      {:dir dir :inventory "inventory.ini"
       :playbooks {:create "main.yml" :delete "main.yml"}
       :extra-vars {:host_alias (ssh-config/host-alias opts)
                    :ip (or (:ip opts) (:ip (fallback-params opts)))
                    :user (or (:user opts) "root")
                    :block_state (if delete? "absent" "present")}}
      (ansible-local-specs opts))))

;; ---------------------------------------------------------------- ansible

(defn inventory [opts]
  (json/generate-string
   {:all {:children {:rybbit {:hosts {(:profile opts)
                                      {:ansible_host (or (:ip opts) (:ip (fallback-params opts)))
                                       :ansible_user (or (:user opts) (:user (fallback-params opts)))}}}}}}
   {:pretty true}))
(defn ansible-data
  "Template values for the Ansible stage. `ssh-private-key-path` reaches
  ansible.cfg so convergence uses the deployment's own key in keygen mode,
  where nothing guarantees an agent holds it."
  [opts]
  (assoc opts
         :ip (or (:ip opts) (:ip (fallback-params opts)))
         :ssh-keygen (validate/keygen? opts) :ssh-identity-present (boolean (:ssh-private-key-path opts))
         :compute-name (or (:name opts) (:name (fallback-params opts)))
         :rybbit-backup-access-key "{{ lookup('env','COLORS_PAR_RYBBIT_BACKUP_R2_ACCESS_KEY_ID') }}"
         :rybbit-backup-secret-key "{{ lookup('env','COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY') }}"))
(defn ansible-specs [opts]
  (let [dir (tool-dir opts ansible-tool) data (ansible-data opts)]
    [(spec (template "ansible" "ansible.cfg") (str dir "/ansible.cfg") data)
     (spec (template "ansible" "main.yml") (str dir "/main.yml") data)
     (spec (template "ansible" "cleanup.yml") (str dir "/cleanup.yml") data)
     (spec (template "ansible" "compose.yml") (str dir "/compose.yml") data)
     (spec (template "ansible" "Caddyfile") (str dir "/Caddyfile") data)
     (spec (template "ansible" "backup") (str dir "/backup") data)
     (raw-spec (str dir "/inventory.json") (inventory data))]))
(defn ansible-step [opts]
  (let [dir (tool-dir opts ansible-tool)]
    (if (and (#{:create :delete} (:green/event opts)) (not (:green/dry-run opts)) (not (:ip opts)))
      (assoc opts :green/exit 1 :green/err "compute node unavailable")
      (ansible/ansible-with-spec opts
        {:dir dir :inventory "inventory.json"
         :playbooks {:create "main.yml" :delete "cleanup.yml"}
         :host-key-checking false}
        (ansible-specs opts)))))

;; --- Acceptance --------------------------------------------------------------
;;
;; Every claim this step reports must be one it checked. TLS is verified (never
;; `curl -k`), an ingested event is read back out of ClickHouse rather than
;; inferred from a status code, and the backup drill is confirmed by a fresh
;; object in R2 rather than by systemd reporting that it started something.

(defn http-status [args]
  (let [r (process/run-with-timeout
           (into ["curl" "-sS" "-o" "/dev/null" "-w" "%{http_code}"] args) {} 20000)]
    (when (zero? (:exit r)) (str/trim (:out r)))))

(defn ssh-out
  "Run `command` on the host over ssh. The deployment's own key is selected in
  keygen mode (`ssh/identity-args`), because nothing guarantees an agent holds
  it; opt-out mode adds nothing and relies on the operator's identities."
  [opts ip command timeout]
  (let [r (process/run-with-timeout
           (-> ["ssh" "-o" "StrictHostKeyChecking=no" "-o" "ConnectTimeout=10"]
               (into (ssh/identity-args opts))
               (conj (str (or (:user opts) "root") "@" ip) (if (= "root" (or (:user opts) "root")) command (str "sudo -n -- sh -c '" (str/replace command "'" "'\"'\"'") "'"))))
           {} timeout)]
    (when (zero? (:exit r)) (str/trim (:out r)))))

(def stack-env "cd /opt/rybbit && set -a && . ./stack.env && set +a && ")

(defn psql [opts ip query]
  (not-empty
   (str (ssh-out opts ip (str stack-env
                         "docker compose exec -T postgres psql -U \"$POSTGRES_USER\""
                         " -d \"$POSTGRES_DB\" -tAc '" query "'")
                 30000))))

(defn clickhouse
  "Resolve the events table from system.tables so the check does not hardcode a
   database name Rybbit's migrations own, then run `query` against it."
  [opts ip query]
  (not-empty
   (str (ssh-out opts ip (str stack-env
                         "t=$(docker compose exec -T clickhouse clickhouse-client"
                         " --user \"$CLICKHOUSE_USER\" --password \"$CLICKHOUSE_PASSWORD\""
                         " --query \"SELECT database || '.' || name FROM system.tables"
                         " WHERE name = 'events' AND database NOT IN ('system')"
                         " ORDER BY database LIMIT 1\" | tr -d '\\r'); "
                         "[ -n \"$t\" ] && docker compose exec -T clickhouse clickhouse-client"
                         " --user \"$CLICKHOUSE_USER\" --password \"$CLICKHOUSE_PASSWORD\""
                         " --query \"" query "\"")
                 30000))))

(defn event-count [opts ip]
  (some-> (clickhouse opts ip "SELECT count() FROM $t") parse-long))

(defn acceptance-site-id
  "A dedicated throwaway site, created on demand. Sending the synthetic event to
   whichever site happened to be first wrote a /colors-acceptance pageview into
   the operator's real analytics on every converge. The site is attached to the
   existing organization so it stays visible and deletable in the UI."
  [opts ip]
  (let [domain (or (not-empty (str (:rybbit-acceptance-site-domain opts)))
                   "colors-acceptance.invalid")]
    ;; Dollar-quoted literals: the query travels inside single quotes in a
    ;; remote shell, where an escaped quote would arrive at psql verbatim.
    ;; psql prints the INSERT tag before the SELECT result, so take the id off
    ;; the last line rather than the whole output.
    (some->> (psql opts ip (str "insert into sites (name, domain, organization_id) "
                  "select $$colors-acceptance$$, $$" domain "$$, "
                  "(select id from organization limit 1) "
                  "where not exists (select 1 from sites where domain = $$" domain "$$); "
                  "select site_id from sites where domain = $$" domain "$$ limit 1"))
             str/split-lines
             last
             str/trim
             (re-matches #"\d+"))))

(defn wait-health [url attempts]
  (loop [n attempts]
    (let [r (process/run-with-timeout ["curl" "-fsS" (str url "/api/health")] {} 10000)]
      (cond (zero? (:exit r)) true
            (pos? n) (do (Thread/sleep 5000) (recur (dec n)))
            :else false))))

(defn send-event
  "Rybbit discriminates on `type`, not `name`: the API answers 400 with
   \"Invalid discriminator value\" for anything else. This went unnoticed while
   no site existed, because the step reports :not-configured and sends nothing."
  [base site]
  (http-status ["-X" "POST" "-H" "content-type: application/json"
                "-H" "User-Agent: Mozilla/5.0 (Colors acceptance)"
                "--data" (json/generate-string
                          {:type "pageview" :site_id site
                           :pathname "/colors-acceptance"})
                (str base "/api/track")]))

(defn ingestion-verdict [status before after]
  (cond (nil? status) :unreachable
        (and (integer? before) (integer? after) (> after before)) :ingested
        (re-matches #"2\d\d" (str status)) :dropped
        :else :rejected))

(defn wait-ingested [opts ip baseline attempts]
  (loop [n attempts]
    (let [after (event-count opts ip)]
      (cond (and (integer? after) (> after baseline)) after
            (pos? n) (do (Thread/sleep 3000) (recur (dec n)))
            :else after))))

(def rclone-env
  (str "RCLONE_CONFIG_R2_TYPE=s3 RCLONE_CONFIG_R2_PROVIDER=Cloudflare "
       "RCLONE_CONFIG_R2_REGION=auto RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true"))

(defn backup-listing [opts ip]
  (some-> (ssh-out opts ip (str "set -a; . /etc/rybbit-backup.env; set +a; " rclone-env
                           " RCLONE_CONFIG_R2_ACCESS_KEY_ID=\"$RYBBIT_BACKUP_R2_ACCESS_KEY_ID\""
                           " RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=\"$RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY\""
                           " RCLONE_CONFIG_R2_ENDPOINT=\"" (:rybbit-backup-r2-endpoint opts) "\""
                           " rclone lsjson --files-only r2:" (:rybbit-backup-r2-bucket opts)
                           "/" (:profile opts))
                   120000)
          not-empty
          (json/parse-string true)))

(defn parse-instant [s]
  (try (.toInstant (java.time.OffsetDateTime/parse (str s))) (catch Exception _ nil)))

(defn fresh-backup? [entries since]
  (boolean (some (fn [{:keys [Size ModTime]}]
                   (and (pos? (or Size 0))
                        (when-let [t (parse-instant ModTime)]
                          (not (.isBefore t since)))))
                 entries)))

(defn run-backup [opts ip]
  (ssh-out opts ip "systemctl start rybbit-backup.service && systemctl is-active rybbit-backup.timer"
           300000))

(defn acceptance-step [opts]
  (if (not= :create (:green/event opts))
    (assoc opts :green/exit 0)
    (let [base (str "https://" (:rybbit-host opts))
          ip (:ip opts)
          since (.minusSeconds (java.time.Instant/now) 120)]
      (if-not (wait-health base 60)
        (assoc opts :green/exit 1
               :green/err "HTTPS health did not become ready with a valid certificate")
        (let [site (acceptance-site-id opts ip)
              before (event-count opts ip)]
          (if-not (integer? before)
            (assoc opts :green/exit 1
                   :green/err "could not read the ClickHouse events table to verify ingestion")
            (let [verdict (if-not site
                            :not-configured
                            (let [status (send-event base site)
                                  after (wait-ingested opts ip before 10)]
                              (ingestion-verdict status before after)))]
              (cond
                (contains? #{:dropped :rejected :unreachable} verdict)
                (assoc opts :green/exit 1
                       :green/err (str "synthetic event was not ingested: " (name verdict)))

                (nil? (run-backup opts ip))
                (assoc opts :green/exit 1 :green/err "backup unit or timer is not healthy")

                (not (fresh-backup? (backup-listing opts ip) since))
                (assoc opts :green/exit 1
                       :green/err (str "no backup object newer than this run under r2:"
                                       (:rybbit-backup-r2-bucket opts) "/" (:profile opts)))

                :else
                (assoc opts :green/exit 0
                       :rybbit/acceptance {:health :ok :event verdict
                                           :backup :verified-in-r2})))))))))
