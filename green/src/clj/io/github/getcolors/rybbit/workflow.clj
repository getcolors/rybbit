(ns io.github.getcolors.rybbit.workflow
 (:require [green.cli :as cli] [green.lifecycle :as lifecycle]
           [green.workflow :as wf] [green.progress :as progress]
           [green.dry-run :as dry-run] [green.tofu :as tofu]
           [io.github.getcolors.rybbit.compute :as compute]
           [io.github.getcolors.rybbit.access :as access]
           [io.github.getcolors.rybbit.reauth :as reauth]
           [io.github.getcolors.rybbit.ssh-config :as ssh-config]
           [io.github.getcolors.rybbit.tools :as tools]
           [io.github.getcolors.rybbit.validate :as validate]))
(def defaults {:provider-compute "vultr" :provider-dns "cloudflare"
               :provider-backend "r2" :compute-prevent-destroy true :workdir ".colors"})
(defn start-step
 ([opts] (start-step opts (System/getenv)))
 ([opts env]
  (lifecycle/preflight opts
   {:defaults defaults :overlay reauth/read-pars
    :validators [(fn [_ env _] (validate/env-errors env))
                 (fn [o _ _] (validate/state-errors o))
                 (fn [o _ c] (when (and (:real? c) (#{:create :delete} (:event c))) (validate/secret-errors o)))
                 (fn [o _ c] (when (and (:real? c) (= :delete (:event c)) (:compute-prevent-destroy o))
                              ["compute destruction is protected; set COLORS_PAR_COMPUTE_PREVENT_DESTROY=false to delete"]))]
    :after-validate
    (fn [o env {:keys [event real?]}]
      (let [real? (and real? (not= :build event))
            o (cond-> o (= :build event) (update :workdir #(str % "/build")))
            _ (when-not (:green/dry-run o) (access/lock! o))
            checked (if (and real? (= :create event)) (ssh-config/preflight! o) o)
            prepared (if (wf/failed? checked) checked (access/resource-step checked))
            prepared (if (wf/failed? prepared) prepared (access/registration-step prepared))
            loaded (if (and real? (not (wf/failed? prepared))
                            (or (#{:delete :ssh} event) (:compute-require-existing-state o)))
                     (compute/load-inventory prepared env) prepared)]
        (cond
          (wf/failed? loaded) loaded
          (:colors-compute/already-destroyed loaded) loaded
          (:rybbit/registration-destroyed loaded)
          (assoc loaded :green/exit 1 :green/err "SSH registration is destroyed while compute is still present")
          :else
          (let [ready (access/agent-step loaded)
                ready (if (and real? (= :delete event) (:ip o)) (assoc ready :ip (:ip o)) ready)]
            ready))))} env)))
(defn wire-fn [step opts]
  (case (:green/event opts)
    :ssh (case step :rybbit/start [start-step :rybbit/ssh] :rybbit/ssh [access/ssh-step] nil)
    :delete
    (case step
      :rybbit/start [start-step :rybbit/ssh-config]
      :rybbit/ssh-config [tools/ansible-local-step :rybbit/ansible]
      :rybbit/ansible [tools/ansible-step :rybbit/dns]
      :rybbit/dns [tools/dns-step :rybbit/infrastructure]
      :rybbit/infrastructure [tools/infrastructure-step :rybbit/registration-delete]
      :rybbit/registration-delete [access/registration-delete-step] nil)
    (case step
      :rybbit/start [start-step :rybbit/infrastructure]
      :rybbit/infrastructure [tools/infrastructure-step :rybbit/ssh-config]
      :rybbit/ssh-config [tools/ansible-local-step :rybbit/dns]
      :rybbit/dns [tools/dns-step :rybbit/ansible]
      :rybbit/ansible [tools/ansible-step :rybbit/acceptance]
      :rybbit/acceptance [tools/acceptance-step] nil)))
(defn backend-advice [tool]
  (tofu/conventional-backend-advice {:dir-fn #(tools/tool-dir % tool)
                                    :key-fn #(str (:profile %) "/" tool ".tfstate")}))
(def side-effecting [:rybbit/infrastructure :rybbit/dns :rybbit/ssh-config :rybbit/ansible
                     :rybbit/acceptance :rybbit/registration-delete :rybbit/ssh])
(defn next-steps [_ successors opts]
  (if (wf/failed? opts) [] (mapv #(vector % opts) successors)))
(def workflow
  (-> (wf/workflow {:start :rybbit/start :wire-fn wire-fn :next-fn next-steps})
      (wf/advice-add :rybbit/dns :before ::backend (backend-advice tools/dns-tool))
      progress/advise (dry-run/advise side-effecting)))
