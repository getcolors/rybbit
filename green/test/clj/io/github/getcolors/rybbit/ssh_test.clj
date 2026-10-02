(ns io.github.getcolors.rybbit.ssh-test
 (:require [clojure.test :refer [deftest is]]
           [io.github.getcolors.rybbit.reauth :as reauth]
           [green.process :as process]
           [babashka.fs :as fs]
           [cheshire.core :as json]
           [io.github.getcolors.rybbit.access :as access]
           [io.github.getcolors.rybbit.compute :as compute]))
(deftest scope-isolates-identities
 (let [args (access/ssh-args {:ip "203.0.113.7" :user "ubuntu"
                              :ssh-private-key-path "/cache/identity.pub" :rybbit/agent-socket "/agent/socket"})]
  (doseq [arg ["IdentityFile=none" "IdentitiesOnly=yes" "IdentityAgent=/agent/socket" "ForwardAgent=no" "ControlMaster=no" "ControlPersist=no"]]
   (is (some #{arg} args)))
  (is (= ["--" "203.0.113.7"] (vec (take-last 2 args))))))
(deftest incomplete-identity-refused
 (is (thrown? Exception (access/ssh-args {:ip "203.0.113.7" :user "ubuntu"}))))
(deftest build-identity-is-public
 (is (= "/home/build-placeholder/compute/demo/ssh/machine-access/identity.pub"
        (:ssh-private-key-path (access/agent-step {:green/event :build :profile "demo"})))))

(def expired {:status "error" :error {:code "command_failed" :stage "plan" :command ["tofu" "plan"]
                                     :infrastructure_changes "none" :auth_reason "google_reauth_required"
                                     :message "Required command failed." :stderr "[structured output suppressed]"}})
(def ssh-opts {:green/event :ssh :provider-compute "google"})
(deftest reauth-retries-once-and-preserves-cancellation
  (doseq [[login-exit second-result expected-calls] [[0 {:status "ready"} 2] [0 expired 2] [130 expired 1] [127 expired 1]]]
    (let [calls (atom 0) log (atom [])]
      (with-redefs [reauth/local-user-adc? (constantly true) reauth/interactive? (constantly true)
                    reauth/announce #(swap! log conj %)
                    process/run-inherit (fn [argv _]
                                          (is (= ["gcloud" "auth" "application-default" "login" "--no-launch-browser"] argv))
                                          {:exit login-exit})]
        (let [result (reauth/resolve-with-login (assoc ssh-opts :rybbit-ssh-login-browser false) {}
                       #(if (= 1 (swap! calls inc)) expired second-result))]
          (is (= expected-calls @calls))
          (is (seq @log))
          (if (zero? login-exit)
            (is (= (:status second-result) (:status result)))
            (is (= login-exit (:green/exit (compute/failed-result {} result)))))
          (when (= second-result expired) (is (not (get-in result [:error :stderr])))))))))
(deftest reauth-does-not-login-outside-interactive-user-ssh
  (doseq [[opts result local? tty?] [[ssh-opts expired true false] [ssh-opts expired false true]
                                    [(assoc ssh-opts :green/event :create) expired true true]
                                    [(assoc ssh-opts :green/event :delete) expired true true]
                                    [(assoc ssh-opts :green/dry-run true) expired true true]
                                    [(assoc ssh-opts :provider-compute "aws") expired true true]
                                    [ssh-opts (assoc-in expired [:error :infrastructure_changes] "possible") true true]
                                    [ssh-opts (update expired :error dissoc :auth_reason) true true]
                                    [ssh-opts {:status "ready"} true true]]]
    (let [calls (atom 0)]
      (with-redefs [reauth/local-user-adc? (constantly local?) reauth/interactive? (constantly tty?)
                    process/run-inherit (fn [& _] (throw (ex-info "unexpected login" {})))]
        (reauth/resolve-with-login opts {} #(do (swap! calls inc) result))
        (is (= 1 @calls))))))
(deftest reauth-checks-local-user-source-without-exposing-secrets
  (let [dir (fs/create-temp-dir) env {"CLOUDSDK_CONFIG" (str dir)} path (str (fs/path dir "application_default_credentials.json"))]
    (try
      (is (false? (boolean (reauth/local-user-adc? env))))
      (doseq [kind ["authorized_user" "service_account" "external_account" "impersonated_service_account"]]
        (spit path (json/generate-string {:type kind :refresh_token "PRIVATE-CANARY"}))
        (is (= (= kind "authorized_user") (boolean (reauth/local-user-adc? env))))
        (doseq [key reauth/credential-overrides]
          (is (false? (boolean (reauth/local-user-adc? (assoc env key "override")))))))
      (spit path "malformed")
      (is (false? (boolean (reauth/local-user-adc? env))))
      (finally (fs/delete-tree dir)))))

(deftest login-uses-sdk-inherited-process-and-retries-resolver
  (let [dir (fs/create-temp-dir) shim (str (fs/path dir "gcloud")) marker (str (fs/path dir "login-args"))
        calls (atom 0) original process/run-inherit]
    (try
      (spit shim "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$LOGIN_MARKER\"\nexit 0\n")
      (.setExecutable (java.io.File. shim) true)
      (with-redefs [reauth/local-user-adc? (constantly true) reauth/interactive? (constantly true)
                    reauth/announce (constantly nil)
                    process/run-inherit (fn [args opts]
                      (is (= "gcloud" (first args)))
                      (original (assoc (vec args) 0 shim) (assoc opts :extra-env {"LOGIN_MARKER" marker})))]
        (is (= "ready" (:status (reauth/resolve-with-login ssh-opts {}
                                  #(if (= 1 (swap! calls inc)) expired {:status "ready"}))))))
      (is (= "auth\napplication-default\nlogin\n" (slurp marker)))
      (is (= 2 @calls))
      (finally (fs/delete-tree dir)))))

(deftest login-browser-environment-is-typed-after-cli-overlay
  (doseq [[value expected] [["false" false] ["true" true] ["FALSE" false] ["invalid" "invalid"]]]
    (is (= expected (:rybbit-ssh-login-browser (reauth/read-pars {:rybbit-ssh-login-browser value}
                       {"COLORS_PAR_RYBBIT_SSH_LOGIN_BROWSER" value}))))))
