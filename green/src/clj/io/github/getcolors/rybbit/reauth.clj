(ns io.github.getcolors.rybbit.reauth
  "Interactive recovery for expired local Google user credentials during SSH."
  (:require [cheshire.core :as json]
            [clojure.java.io :as io]
            [clojure.string :as str]
            [green.process :as process]
            [green.cli :as cli]))

(def credential-overrides
  ["GOOGLE_APPLICATION_CREDENTIALS" "GOOGLE_CREDENTIALS" "GOOGLE_CLOUD_KEYFILE_JSON"
   "GCLOUD_KEYFILE_JSON" "GOOGLE_OAUTH_ACCESS_TOKEN" "GOOGLE_IMPERSONATE_SERVICE_ACCOUNT"
   "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE" "CLOUDSDK_AUTH_ACCESS_TOKEN"
   "CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT"])
(defn read-pars [opts env]
  ;; The CLI overlays unknown keys as strings before workflow defaults exist.
  ;; Seed this optional boolean's type before applying its environment override.
  (cli/read-pars (cond-> opts (contains? env "COLORS_PAR_RYBBIT_SSH_LOGIN_BROWSER")
                  (assoc :rybbit-ssh-login-browser true)) env))
(def login-command ["gcloud" "auth" "application-default" "login"])
(defn interactive? [] (some? (System/console)))
(defn announce [message] (binding [*out* *err*] (println message) (flush)))
(defn local-user-adc? [env]
  (and (not-any? #(not (str/blank? (get env %))) credential-overrides)
       (try
         (let [directory (or (not-empty (get env "CLOUDSDK_CONFIG"))
                             (str (io/file (or (not-empty (get env "HOME")) (System/getProperty "user.home")) ".config" "gcloud")))]
           (= "authorized_user" (:type (json/parse-string (slurp (io/file directory "application_default_credentials.json")) true))))
         (catch Exception _ false))))
(defn recoverable? [opts result]
  (let [error (:error result)]
    (and (= :ssh (:green/event opts)) (not (:green/dry-run opts))
         (= "google" (:provider-compute opts)) (= "error" (:status result))
         (= "google_reauth_required" (:auth_reason error))
         (= "plan" (:stage error)) (= ["tofu" "plan"] (:command error))
         (= "none" (:infrastructure_changes error)))))
(defn explain [result message]
  (update result :error #(assoc (dissoc % :stderr) :message message)))
(defn resolve-with-login [opts env resolve!]
  (let [result (resolve!)]
    (if-not (recoverable? opts result) result
      (let [local? (local-user-adc? env)
            command (cond-> login-command (false? (:rybbit-ssh-login-browser opts)) (conj "--no-launch-browser"))
            hint (if local?
                   (str "Run `" (str/join " " command) "`, then retry SSH.")
                   "Renew the configured Google credentials, then retry SSH; automatic login requires local user ADC without credential overrides.")]
        (if-not (and local? (interactive?))
          (explain result (str "Google authentication expired. " hint))
          (do
            (announce "Google authentication expired. Starting Google sign-in…")
            (announce (str "$ " (str/join " " command)))
            (let [login (process/run-inherit command {})]
              (if (zero? (or (:exit login) 1))
                (do (announce "Google authentication complete. Retrying address lookup…")
                    (let [retried (resolve!)]
                      (if (recoverable? opts retried)
                        (explain retried "Google authentication is still expired after login. Check the configured credential source; SSH was not started.")
                        retried)))
                (assoc (explain result (if (#{126 127} (:exit login))
                                        "Google sign-in could not start. Check that `gcloud` is installed and available in this terminal, then retry SSH."
                                        "Google sign-in did not complete. SSH was not started; retry SSH to try again."))
                       :rybbit/login-exit (or (:exit login) 1))))))))))
