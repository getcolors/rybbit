(ns io.github.getcolors.rybbit.access
  "V2 encrypted authority, profile ownership and scoped administrative access."
  (:require [clojure.java.io :as io] [clojure.string :as str] [green.cli :as cli] [green.scope :as scope]
            [green.process :as process]
            [io.github.getcolors.compute-local :as local]
            [io.github.getcolors.compute-node :as node]
            [io.github.getcolors.compute-ssh :as ssh]
            [io.github.getcolors.rybbit.compute :as machine])
  (:import [java.nio.channels FileChannel]
           [java.nio.file StandardOpenOption LinkOption OpenOption]))
(def ^:dynamic *register!* nil)
(defn scoped [f]
  (scope/with-scope (fn [register!] (binding [*register!* register!] (f)))))
(defn run-cli [workflow args] (scoped #(cli/run-cli workflow args)))
(defn lock! [opts]
  (when-not *register!* (throw (ex-info "Rybbit runtime requires an access scope" {})))
  (let [root (machine/sdk-workdir opts)
        directory (local/path (str (io/file root (:profile opts))))
        path (.resolve directory ".rybbit.lock")]
    (local/private-owned-directory! root directory)
    (local/prepare! (str path))
    (let [channel (FileChannel/open path (into-array OpenOption [StandardOpenOption/CREATE StandardOpenOption/WRITE LinkOption/NOFOLLOW_LINKS]))]
      (try
        (local/prepare! (str path))
        (when-not (.tryLock channel) (throw (ex-info "another Rybbit operation owns this profile" {})))
        (*register!* :resource #(.close channel))
        (catch Exception e (.close channel) (throw e))))))
(defn absence-request [opts]
  {:workdir (machine/sdk-workdir opts)
   :consumers [{:node_id machine/node-id :state_filename machine/state-filename}]
   :registrations (if (machine/registration? opts)
                    [{:name "machine-access" :state_filename "rybbit-ssh-registration.tfstate"}]
                    [])})
(defn resource-step [opts]
  (let [options (machine/library-options opts)
        request (machine/ssh-request opts)
        env (System/getenv)
        inspected (if (machine/planning? opts) machine/placeholder-resource
                      (ssh/ssh-resource! options request "inspect" env))
        result (if (and (= :create (:green/event opts))
                        (not (machine/planning? opts))
                        (not (:compute-require-existing-state opts))
                        (= "error" (:status inspected))
                        (= "ssh_authority_missing" (get-in inspected [:error :code])))
                 (let [verification (ssh/ssh-verify-absent! options (absence-request opts) env)]
                   (if (and (= "verified" (:status verification)) (true? (:verified_absent verification)))
                     (ssh/ssh-resource! options (assoc request :verified_absent true) "create" env)
                     (if (= "error" (:status verification)) verification
                         {:status "error" :error {:message "SSH consumer absence was not verified"}})))
                 inspected)]
    (if (= "ready" (:status result)) (assoc opts :rybbit/ssh-resource result :green/exit 0)
        (machine/failed-result opts result))))
(defn registration-step [opts]
  (cond
    (not (machine/registration? opts)) opts
    (:green/dry-run opts) (assoc opts :rybbit/ssh-registration (machine/placeholder-registration opts) :green/exit 0)
    :else
    (let [operation (if (and (= :create (:green/event opts)) (not (:compute-require-existing-state opts))) "create" "inspect")
          result (if (machine/planning? opts)
                   (machine/canonical-build! opts (node/registration-plan (machine/library-options opts) (machine/registration-request opts)))
                   (node/compute-registration! (machine/library-options opts) (machine/registration-request opts) operation))]
      (case (:status result)
        "built" (assoc opts :rybbit/ssh-registration (machine/placeholder-registration opts) :green/exit 0)
        "ready" (assoc opts :rybbit/ssh-registration result :green/exit 0)
        ;; A synthetic registration is only used to inspect already-empty compute
        ;; state. Never converge or destroy a live node with this placeholder.
        "destroyed" (if (= :delete (:green/event opts))
                      (assoc opts :rybbit/registration-destroyed true :rybbit/ssh-registration (machine/placeholder-registration opts))
                      (machine/failed-result opts result))
        (machine/failed-result opts result)))))
(defn agent-step [opts]
  (if (machine/planning? opts)
    (assoc opts :ssh-private-key-path (machine/placeholder-key opts) :rybbit/agent-socket "/home/build-placeholder/agent.sock")
    (let [agent (ssh/start-agent! [{:opts (machine/library-options opts) :request (machine/ssh-request opts)
                                  :resource (machine/resource opts)}] (System/getenv) *register!*)]
      (assoc opts :rybbit/agent-socket (:socket agent)
             :ssh-private-key-path (get (:identities agent) (:reference (machine/resource opts)))))))
(defn registration-delete-step [opts]
  (if (or (not (machine/registration? opts)) (:rybbit/registration-destroyed opts)) opts
    (let [result (node/compute-registration! (machine/library-options opts) (machine/registration-request opts) "delete")]
      (if (= "destroyed" (:status result)) (assoc opts :green/exit 0) (machine/failed-result opts result)))))
(defn identity-args [opts]
  (when-let [identity (:ssh-private-key-path opts)]
    ["-F" "/dev/null" "-o" "IdentityFile=none" "-i" identity "-o" "IdentitiesOnly=yes" "-o" (str "IdentityAgent=" (or (:rybbit/agent-socket opts) "none"))
     "-o" "ForwardAgent=no" "-o" "ControlMaster=no" "-o" "ControlPersist=no" "-S" "none"]))
(defn ssh-args [opts]
  (when-not (every? #(and (string? %) (not (str/blank? %)))
                    [(:ip opts) (:user opts) (:ssh-private-key-path opts) (:rybbit/agent-socket opts)])
    (throw (ex-info "SSH requires a resolved address, login and scoped identity" {})))
  (into ["ssh" "-p" "22" "-l" (:user opts)
         "-o" "StrictHostKeyChecking=accept-new"]
        (concat (identity-args opts) ["--" (:ip opts)])))
(defn ssh-step [opts]
  (let [result (process/run-inherit (ssh-args opts) {})]
    (assoc opts :green/exit (or (:exit result) 1))))
