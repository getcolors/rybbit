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
            [io.github.getcolors.rybbit.validate :as validate]))

(def infrastructure-tool "rybbit-infrastructure")
(def dns-tool "rybbit-dns")
(def ansible-tool "rybbit-ansible")
(def root "io.github.getcolors.rybbit.tools")
(def template-opts sc/preserve-jinja-delimiters)
(defn tool-dir [opts tool] (green-cli/stage-dir opts tool {:default-profile "rybbit"}))
(defn template [path file] (keyword (str root "." path) file))
(defn spec [source target data] {:template source :target target :data data :opts template-opts})
(defn raw-spec [target content] (sc/content-spec target content))
(defn cidrs [opts k]
  (let [v (get opts k) xs (if (sequential? v) v (str/split (str v) #"[,\s]+"))]
    (->> xs (map (comp str/trim str)) (remove str/blank?) vec)))
(defn credential-env [opts & slots]
  (not-empty
   (into {} (keep (fn [[k env-var]]
                    (when-let [v (not-empty (str (get opts k)))] [env-var v])))
         (apply merge (map #(validate/tofu-env opts %) (conj (vec slots) :provider-backend))))))
(defn backend-credential-env [opts] (credential-env opts))
(defn fallback-params [opts]
  {:ip "192.0.2.10" :user "root" :sudoer "root" :name (:profile opts)})
(defn output-params [result]
  (some-> (get-in result [:tofu/outputs :params]) walk/keywordize-keys))

(defn infrastructure-data [opts]
  (assoc opts
         :ssh-sources-hcl (tofu/hcl-list (cidrs opts :digitalocean-ssh-sources))
         :http-sources-hcl (tofu/hcl-list (cidrs opts :digitalocean-http-sources))))
(defn infrastructure-step [opts]
  (let [dir (tool-dir opts infrastructure-tool)
        specs [(spec (template "infrastructure" "main.tf") (str dir "/main.tf")
                     (infrastructure-data opts))]
        result (tofu/tofu-with-spec opts specs
                                    {:dir dir :env (credential-env opts :provider-compute)})]
    (cond
      (wf/failed? result) result
      (= :build (:green/event opts)) (merge result (fallback-params opts))
      (= :delete (:green/event opts)) result
      :else (merge result (fallback-params opts) (output-params result)))))

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
           :cloudflare-proxied (if (some? (:cloudflare-proxied opts))
                                 (:cloudflare-proxied opts)
                                 false))))

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

(defn inventory [opts]
  (json/generate-string
   {:all {:children {:rybbit {:hosts {(:profile opts)
                                      {:ansible_host (or (:ip opts) "192.0.2.10")
                                       :ansible_user "root"}}}}}}
   {:pretty true}))
(defn ansible-data [opts]
  (assoc opts
         :ip (or (:ip opts) "192.0.2.10")
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
    (ansible/ansible-with-spec opts
      {:dir dir :inventory "inventory.json"
       :playbooks {:create "main.yml" :delete "cleanup.yml"}
       :host-key-checking false}
      (ansible-specs opts))))

(defn run-json [args timeout]
  (let [r (process/run-with-timeout args {} timeout)]
    (if (zero? (:exit r))
      [(try (json/parse-string (:out r) true) (catch Exception _ (:out r))) nil]
      [nil (str (:err r) (:out r))])))

(defn wait-health [url attempts]
  (loop [n attempts]
    (let [r (process/run-with-timeout ["curl" "-fsS" (str url "/api/health")] {} 10000)]
      (cond (zero? (:exit r)) true
            (pos? n) (do (Thread/sleep 5000) (recur (dec n)))
            :else false))))

(defn acceptance-step [opts]
  (if (not= :create (:green/event opts))
    (assoc opts :green/exit 0)
    (let [base (str "https://" (:rybbit-host opts))]
      (if-not (wait-health base 60)
        (assoc opts :green/exit 1 :green/err "HTTPS health did not become ready")
        (let [track-res (process/run-with-timeout
                         ["curl" "-s" "-o" "/dev/null" "-w" "%{http_code}" "-X" "POST"
                          "-H" "content-type: application/json"
                          "--data" "{\"name\":\"pageview\",\"site_id\":\"benchmark\",\"data\":{\"path\":\"/acceptance-test\"}}"
                          (str base "/api/track")] {} 15000)
              backup-res (process/run-with-timeout
                          ["ssh" "-o" "StrictHostKeyChecking=no" "-o" "ConnectTimeout=10"
                           (str "root@" (:ip opts)) "systemctl start rybbit-backup.service"] {} 30000)
              timer-res (process/run-with-timeout
                         ["ssh" "-o" "StrictHostKeyChecking=no" "-o" "ConnectTimeout=10"
                          (str "root@" (:ip opts)) "systemctl is-active rybbit-backup.timer"] {} 10000)]
          (cond
            (not (zero? (:exit backup-res)))
            (assoc opts :green/exit 1 :green/err (str "backup service run failed: " (:err backup-res) (:out backup-res)))
            (not= "active\n" (:out timer-res))
            (assoc opts :green/exit 1 :green/err (str "backup timer is not active: " (:out timer-res)))
            :else
            (assoc opts :green/exit 0 :rybbit/acceptance {:health "ok" :track (:out track-res) :backup "ok"})))))))
