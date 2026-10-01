;; Pure v2 behavior parity: no provider calls, state or operator files.
(require '[clj-yaml.core :as yaml] '[cheshire.core :as json]
         '[io.github.getcolors.rybbit.compute :as compute]
         '[io.github.getcolors.rybbit.access :as access]
         '[io.github.getcolors.rybbit.workflow :as workflow]
         '[io.github.getcolors.rybbit.tools :as tools])
(def opts (assoc (yaml/parse-string (slurp (first *command-line-args*))) :workdir "/tmp/rybbit-runtime-parity" :green/event :build))
(defn graph [event]
  (loop [step :rybbit/start path []]
    (if step (recur (second (workflow/wire-fn step (assoc opts :green/event event))) (conj path (subs (str step) 1))) path)))
(defn source-errors [suffix value missing?]
 (let [state (apply dissoc opts (map #(keyword (str % "-" suffix)) ["compute" "rybbit" (:provider-compute opts)]))]
  (compute/errors (if missing? state (assoc state (keyword (str "compute-" suffix)) value)))))
(def req (compute/request opts))
(def output
  {:backupChildEnv (into {} (for [event [:create :build :delete]] [event (tools/ansible-secret-env (assoc opts :green/event event :rybbit-backup-r2-access-key-id "dummy-access" :rybbit-backup-r2-secret-access-key "dummy-secret" :rybbit-ssh-passphrase "never-forward"))]))
   :legacyReferences (mapv #(compute/errors (assoc opts % nil)) [:digitalocean-ssh-keys :vultr-ssh-keys :ssh-key-id])
   :failures (mapv #(get (compute/failed-result opts %) :green/err) [{} {:error nil} {:error {:message nil}} {:error {:message "refused" :stderr "provider detail"}}])
   :sourceErrors (into {} (for [suffix ["ssh-sources" "http-sources"]]
    [suffix (into {} (for [[label value] [["missing" nil] ["false" false] ["map" {}] ["null" nil]]]
      [label (source-errors suffix value (= label "missing"))]))]))
   :errors {:missingV2 (compute/errors (dissoc opts :compute-api-version))
            :legacyKey (compute/errors (assoc opts :ssh-private-key-path "/tmp/operator-key"))}
   :requirements (compute/requirements opts)
   :closedHttp (compute/requirements (assoc opts :compute-http-sources []))
   :sourcePrecedence (get-in (compute/requirements (assoc opts :compute-ssh-sources ["192.0.2.1/32"] :rybbit-ssh-sources ["192.0.2.2/32"] (keyword (str (:provider-compute opts) "-ssh-sources")) ["192.0.2.3/32"])) [:ingress 0 :sources])
   :identity {:node (:node_id req) :state (:state_filename req) :passphrase (:passphrase_env (compute/ssh-request opts)) :registration (compute/registration? opts)}
   :ssh (access/ssh-args (assoc opts :ip "203.0.113.1" :user "ubuntu" :ssh-private-key-path "/tmp/public.pub" :rybbit/agent-socket "/tmp/agent.sock"))
   :graphs (into {} (map (fn [event] [event (graph event)]) [:create :delete :ssh]))})
(defn sorted [value]
  (cond (map? value) (into (sorted-map) (map (fn [[k v]] [k (sorted v)]) value))
        (sequential? value) (mapv sorted value)
        :else value))
(println (json/generate-string (sorted output)))
