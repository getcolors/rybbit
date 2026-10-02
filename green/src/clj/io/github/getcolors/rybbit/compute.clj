(ns io.github.getcolors.rybbit.compute
  "Rybbit's stable v2 singleton and application parameter adapter."
  (:require [cheshire.core :as json] [clojure.java.io :as io] [clojure.string :as str]
            [green.cli :as cli]
            [io.github.getcolors.rybbit.reauth :as reauth]
            [io.github.getcolors.compute :as compute]
            [io.github.getcolors.compute-node :as node]
            [io.github.getcolors.compute-local :as local]
            [io.github.getcolors.compute-ssh :as ssh]))

(def node-id "rybbit-compute")
(def state-filename "rybbit-node-0.tfstate")
(defn planning? [opts] (or (= :build (:green/event opts)) (:green/dry-run opts)))
(defn sdk-workdir [opts]
  (-> (cli/stage-dir opts node-id) io/file .getAbsoluteFile .getParentFile .getParentFile .toPath .normalize str))
(defn library-options [opts]
  (into {} (remove (fn [[key _]] (or (namespace key) (#{:ssh-private-key-path :ssh-public-key-path :rybbit-ssh-passphrase} key))) opts)))
(def placeholder-resource
  {:status "ready" :reference "ssh-resource:build-placeholder"
   :public_key "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
   :fingerprint "SHA256:kmYcvdi2GkPeWxB6XLjrZB8JHsy2Hm8luHMFp9GMvqk"})
(defn resource [opts]
  (or (:rybbit/ssh-resource opts) (when (planning? opts) placeholder-resource)
      (throw (ex-info "SSH resource unavailable" {}))))
(defn ssh-request [opts]
  {:name "machine-access" :workdir (sdk-workdir opts) :passphrase_env "COLORS_PAR_RYBBIT_SSH_PASSPHRASE"})
(defn registration? [opts]
  (boolean (get-in compute/registry [:compute (keyword (:provider-compute opts)) :registration])))
(defn registration-request [opts]
  {:name "machine-access" :workdir (sdk-workdir opts)
   :state_filename "rybbit-ssh-registration.tfstate" :ssh_resource (resource opts)})
(defn placeholder-registration [opts]
  {:status "ready" :reference "registration:build-placeholder" :provider (:provider-compute opts)
   :ssh_resource_reference (:reference (resource opts)) :fingerprint (:fingerprint (resource opts)) :id "0"})
(defn sources [opts suffix]
  (let [value (first (keep (fn [key] (let [value (get opts key)] (when (some? value) [value])))
                           [(keyword (str "compute-" suffix)) (keyword (str "rybbit-" suffix))
                            (keyword (str (:provider-compute opts) "-" suffix))]))
        value (first value)]
    (if (string? value) (vec (remove str/blank? (str/split value #"[,\s]+"))) value)))
(defn requirements [opts]
  (let [ssh (sources opts "ssh-sources") http (sources opts "http-sources")]
    (when-not (and (sequential? ssh) (seq ssh)) (throw (ex-info "compute-ssh-sources is required" {})))
    (when-not (sequential? http) (throw (ex-info "compute-http-sources is required" {})))
    {:egress "all" :private_filter false
     :ingress (into [{:id "ssh" :protocol "tcp" :from_port 22 :to_port 22 :sources ssh}]
                    (when (seq http)
                      [{:id "http-80" :protocol "tcp" :from_port 80 :to_port 80 :sources http}
                       {:id "http-443" :protocol "tcp" :from_port 443 :to_port 443 :sources http}
                       {:id "http3" :protocol "udp" :from_port 443 :to_port 443 :sources http}]))}))
(defn request [opts]
  (cond-> {:node_id node-id :state_filename state-filename :workdir (sdk-workdir opts)
           :ssh_resource (resource opts) :security (requirements opts)}
    (:compute-network-mode opts) (assoc :network {:mode (:compute-network-mode opts)})
    (registration? opts) (assoc :ssh_registration (or (:rybbit/ssh-registration opts)
                                                     (when (planning? opts) (placeholder-registration opts))))))
(defn errors [opts]
  (cond
    (not= 2 (:compute-api-version opts)) ["compute-api-version must be 2; existing deployments must retain their pinned launchers"]
    (not (#{"r2" "s3"} (:provider-backend opts))) ["compute state requires an s3 or r2 backend"]
    (some #(contains? opts %)
          (concat [:ssh-key-path :ssh-private-key-path :ssh-public-key-path :ssh-keygen :ssh-key-id]
                  (mapcat (fn [adapter]
                            (keep #(when (or (string? %) (keyword? %)) (keyword %))
                                  (cons (:ssh-setting adapter) (keys (:ssh-aliases adapter)))))
                          (vals (:compute compute/registry)))))
    ["external SSH keys are outside the single-node contract"]
    :else (try
            (let [opts (assoc opts :green/dry-run true)]
              (ssh/ssh-plan (library-options opts) (ssh-request opts))
              (node/node-plan (library-options opts) (request opts)))
            [] (catch Exception e [(.getMessage e)]))))
(defn placeholder-key [opts]
  (str "/home/build-placeholder/compute/" (:profile opts) "/ssh/machine-access/identity.pub"))
(defn params [opts result]
  (let [data (:params result)]
    (assoc data :name (or (:name data) (str (:profile opts) "-" node-id)) :sudoer (or (:sudoer data) (:user data)) :ssh-keygen true
           :ssh-private-key-path (or (:ssh-private-key-path opts) (when (planning? opts) (placeholder-key opts)))
           :rybbit/agent-socket (:rybbit/agent-socket opts))))
(defn fallback-params [opts]
  (when-not (planning? opts) (throw (ex-info "compute inventory unavailable" {})))
  (params opts {:params {:node_id node-id :provider (:provider-compute opts) :ip "192.0.2.10"
                        :user (get-in compute/registry [:compute (keyword (:provider-compute opts)) :user])}}))
(defn failed-result [opts result]
  (let [message (or (get-in result [:error :message]) "compute lifecycle refused")
        stderr (get-in result [:error :stderr])]
    ;; The library sanitizes provider diagnostics before returning its result.
    (assoc opts :green/exit (or (:rybbit/login-exit result) 1) :green/err (str message (when (seq stderr) (str "\n" stderr))))))
(defn adopt [opts result]
  (let [data (params opts result)]
    (assoc (merge opts data) :rybbit/compute-params data :colors-compute/node (:params result) :green/exit 0)))
(defn- compute-json [value indent]
  (let [padding #(apply str (repeat % " "))]
    (cond
      (map? value) (if (empty? value) "{}"
                      (str "{\n" (str/join ",\n" (for [[key item] (sort-by (fn [[key _]] (if (keyword? key) (subs (str key) 1) (str key))) value)]
                                                       (str (padding (+ indent 2)) (json/generate-string key) ": " (compute-json item (+ indent 2)))))
                           "\n" (padding indent) "}"))
      (sequential? value) (if (empty? value) "[]"
                              (str "[\n" (str/join ",\n" (map #(str (padding (+ indent 2)) (compute-json % (+ indent 2))) value)) "\n" (padding indent) "]"))
      :else (json/generate-string value))))
(defn canonical-build! [opts result]
  (local/private-owned-directory! (sdk-workdir opts) (local/path (:directory result)))
  (doseq [[filename document] (:documents result)]
    (let [target (str (io/file (:directory result) filename))]
      (local/prepare! target)
      (local/write-atomic! target (str (compute-json document 0) "\n"))))
  (assoc result :status "built"))
(defn step [opts]
  (let [result (if (planning? opts)
                 (canonical-build! opts (node/node-plan (library-options opts) (request opts)))
                 (node/compute-node! (library-options opts) (request opts)
                                     (if (= :delete (:green/event opts)) "delete" "create")))]
    (case (:status result)
      "built" (let [data (fallback-params opts)] (assoc (merge opts data) :rybbit/compute-params data :green/exit 0))
      "ready" (adopt opts result)
      "destroyed" (assoc opts :green/exit 0)
      (failed-result opts result))))
(defn load-inventory
  ([opts] (load-inventory opts (System/getenv)))
  ([opts env]
   (let [result (if (#{:describe :ssh} (:green/event opts))
                  (reauth/resolve-with-login opts env #(node/resolve-connection! (library-options opts) (request opts) env))
                  (node/compute-node! (library-options opts) (request opts) "inspect" env))]
     (case (:status result)
       "ready" (adopt opts result)
       "destroyed" (if (= :delete (:green/event opts))
                     (assoc opts :green/exit 0 :colors-compute/already-destroyed true)
                     (assoc opts :green/exit 1 :green/err "compute node is destroyed"))
       (failed-result opts result)))))

(defn infrastructure-step [opts]
  (if (:colors-compute/already-destroyed opts) opts (step opts)))
(def load-step load-inventory)
