(ns io.github.getcolors.rybbit.workflow
  (:require [clojure.walk :as walk]
            [green.cli :as green-cli]
            [green.dry-run :as dry-run]
            [green.lifecycle :as lifecycle]
            [green.progress :as progress]
            [green.tofu :as tofu]
            [green.workflow :as wf]
            [io.github.getcolors.rybbit.tools :as tools]
            [io.github.getcolors.rybbit.validate :as validate]))

(def defaults {:provider-compute "digitalocean" :provider-dns "cloudflare"
               :provider-backend "local" :compute-prevent-destroy true
               :workdir ".colors"})
(defn state-output [opts]
  (try (some-> (tofu/outputs (tools/tool-dir opts tools/infrastructure-tool)
                             (tools/backend-credential-env opts))
               :params walk/keywordize-keys)
       (catch Exception _ nil)))
(defn start-step
  ([opts] (start-step opts (System/getenv)))
  ([opts env]
   (lifecycle/preflight
    opts {:defaults defaults :overlay green-cli/read-pars
          :validators
          [(fn [_ env _] (validate/env-errors env))
           (fn [opts _ _] (validate/state-errors opts))
           (fn [opts _ {:keys [event real?]}]
             (when (and real? (contains? #{:create :delete} event))
               (validate/secret-errors opts)))
           (fn [opts _ {:keys [event real?]}]
             (when (and real? (= :delete event) (:compute-prevent-destroy opts))
               [(str "compute destruction is protected; set "
                     (green-cli/par-name :compute-prevent-destroy) "=false to delete")]))]
          :after-validate
          (fn [opts _ {:keys [event real?]}]
            (if (and real? (= :delete event))
              (merge opts (or (state-output opts) {}) {:green/exit 0})
              (assoc opts :green/exit 0)))} env)))
(defn wire-fn [step run-opts]
  (if (= :delete (:green/event run-opts))
    (case step
      :rybbit/start [start-step :rybbit/ansible]
      :rybbit/ansible [tools/ansible-step :rybbit/dns]
      :rybbit/dns [tools/dns-step :rybbit/infrastructure]
      :rybbit/infrastructure [tools/infrastructure-step])
    (case step
      :rybbit/start [start-step :rybbit/infrastructure]
      :rybbit/infrastructure [tools/infrastructure-step :rybbit/dns]
      :rybbit/dns [tools/dns-step :rybbit/ansible]
      :rybbit/ansible [tools/ansible-step :rybbit/acceptance]
      :rybbit/acceptance [tools/acceptance-step])))
(defn backend-advice [tool]
  (tofu/conventional-backend-advice
   {:dir-fn #(tools/tool-dir % tool)
    :key-fn #(str (:profile %) "/" tool ".tfstate")}))
(def side-effecting [:rybbit/infrastructure :rybbit/dns :rybbit/ansible :rybbit/acceptance])
(def workflow
  (-> (wf/workflow {:start :rybbit/start :wire-fn wire-fn})
      (wf/advice-add :rybbit/infrastructure :before ::backend
                     (backend-advice tools/infrastructure-tool))
      (wf/advice-add :rybbit/dns :before ::backend (backend-advice tools/dns-tool))
      progress/advise
      (dry-run/advise side-effecting)))
