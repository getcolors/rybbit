(ns io.github.getcolors.rybbit.ssh-test
 (:require [clojure.test :refer [deftest is]]
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
