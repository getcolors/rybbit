(ns io.github.getcolors.rybbit.workflow-test
 (:require [clojure.test :refer [deftest is]]
           [babashka.fs :as fs]
           [io.github.getcolors.compute-ssh :as library-ssh]
           [io.github.getcolors.rybbit.ssh-config :as ssh-config]
           [io.github.getcolors.rybbit.workflow :as workflow]
           [io.github.getcolors.rybbit.access :as access]
           [io.github.getcolors.rybbit.compute :as compute]
           [io.github.getcolors.compute-node :as node]
           [io.github.getcolors.rybbit.validate :as validate]
           [io.github.getcolors.rybbit.validate-test :refer [fixture keygen vultr-fixture]]))
(deftest singleton-library-contract
 (is (= "rybbit-compute" compute/node-id))
 (is (= "rybbit-node-0.tfstate" compute/state-filename))
 (is (= "COLORS_PAR_RYBBIT_SSH_PASSPHRASE" (:passphrase_env (compute/ssh-request (fixture))))))
(deftest ingress-and-empty-http
 (is (= ["tcp" "tcp" "tcp" "udp"] (mapv :protocol (:ingress (compute/requirements (fixture))))))
 (is (= [22] (mapv :from_port (:ingress (compute/requirements (fixture :compute-http-sources [])))))))
(deftest reject-legacy-and-no-live-fallback
 (is (seq (compute/errors (fixture :compute-api-version 1))))
 (is (thrown? Exception (compute/fallback-params (fixture :green/event :create)))))
(deftest login-survives-inventory
 (is (= "ubuntu" (:user (compute/adopt {} {:params {:ip "203.0.113.7" :user "ubuntu"}})))))
(deftest destruction-inspection
 (with-redefs [node/compute-node! (fn [& _] {:status "destroyed"})]
  (is (:colors-compute/already-destroyed
        (compute/load-inventory (assoc (fixture :green/event :delete) :rybbit/ssh-resource compute/placeholder-resource) {})))))
(deftest lifecycle-order
 (is (= :rybbit/ssh-config (second (workflow/wire-fn :rybbit/start {:green/event :delete}))))
 (is (= :rybbit/registration-delete (second (workflow/wire-fn :rybbit/infrastructure {:green/event :delete}))))
 (is (= [] (workflow/next-steps :rybbit/start [:rybbit/infrastructure] {:green/exit 1}))))
(deftest library-document-keys-render-deterministically
 (is (= (#'compute/compute-json {"a" 0 :b 1} 0) (#'compute/compute-json {:a 0 "b" 1} 0))))

(deftest deletion-resolves-before-opening-agent
 (let [calls (atom [])]
  (with-redefs [validate/state-errors (constantly []) validate/secret-errors (constantly [])
                access/lock! (fn [_] (swap! calls conj :lock))
                access/resource-step #(do (swap! calls conj :resource) %)
                access/registration-step #(do (swap! calls conj :registration) %)
                compute/load-inventory (fn [o _] (swap! calls conj :inspect) (assoc o :ip "203.0.113.7" :user "ubuntu"))
                access/agent-step #(do (swap! calls conj :agent) %)]
   (is (= "203.0.113.7" (:ip (workflow/start-step {:green/event :delete :compute-prevent-destroy false} {}))))
   (is (= [:lock :resource :registration :inspect :agent] @calls)))))
(deftest destroyed-node-needs-no-agent
 (with-redefs [validate/state-errors (constantly []) validate/secret-errors (constantly [])
               access/lock! (constantly nil) access/resource-step identity access/registration-step identity
               compute/load-inventory (fn [o _] (assoc o :colors-compute/already-destroyed true))
               access/agent-step (fn [_] (throw (ex-info "agent must not start" {})))]
  (is (:colors-compute/already-destroyed
       (workflow/start-step {:green/event :delete :compute-prevent-destroy false} {})))))
(deftest lost-registration-refuses-live-cleanup
 (with-redefs [validate/state-errors (constantly []) validate/secret-errors (constantly [])
               access/lock! (constantly nil) access/resource-step identity
               access/registration-step #(assoc % :rybbit/registration-destroyed true)
               compute/load-inventory (fn [o _] (assoc o :ip "203.0.113.7"))
               access/agent-step (fn [_] (throw (ex-info "agent must not start" {})))]
  (is (= 1 (:green/exit (workflow/start-step {:green/event :delete :compute-prevent-destroy false} {}))))))

(deftest dry-run-does-not-touch-workdir-or-runtime
 (let [root (str (fs/create-temp-dir {:prefix "rybbit-dry-run-"})) workdir (str root "/absent")]
  (try
   (with-redefs [access/lock! (fn [_] (throw (ex-info "lock" {})))
                 ssh-config/preflight! (fn [_] (throw (ex-info "operator config" {})))
                 library-ssh/ssh-resource! (fn [& _] (throw (ex-info "SSH authority" {})))
                 node/compute-registration! (fn [& _] (throw (ex-info "registration" {})))
                 node/compute-node! (fn [& _] (throw (ex-info "compute" {})))]
    (doseq [f [keygen vultr-fixture]]
     (is (= 0 (:green/exit (workflow/start-step (f :workdir workdir :green/event :create :green/dry-run true) {}))))))
   (is (not (fs/exists? workdir)))
   (finally (fs/delete-tree root)))))
(deftest conflicting-alias-refuses-before-authority-creation
 (with-redefs [validate/state-errors (constantly []) validate/secret-errors (constantly [])
               access/lock! (constantly nil)
               ssh-config/preflight! #(assoc % :green/exit 1 :green/err "foreign alias")
               access/resource-step (fn [_] (throw (ex-info "must not create authority" {})))]
  (is (= "foreign alias" (:green/err (workflow/start-step {:green/event :create} {}))))))

(deftest library-diagnostics-are-preserved
 (doseq [result [{} {:error nil} {:error {:message nil}}]]
  (is (= "compute lifecycle refused" (:green/err (compute/failed-result {} result)))))
 (is (= "provider refused\nrequest ID abc" (:green/err (compute/failed-result {} {:error {:message "provider refused" :stderr "request ID abc"}}))))
 (is (= "provider refused" (:green/err (compute/failed-result {} {:error {:message "provider refused" :stderr ""}})))))
