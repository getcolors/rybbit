(ns io.github.getcolors.rybbit.tools-test
  (:require [clojure.string :as str]
            [clojure.test :refer [deftest is]]
            [io.github.getcolors.rybbit.tools :as tools]
            [io.github.getcolors.rybbit.validate-test :refer [fixture]]))

(deftest infrastructure-discovers-default-vpc
  (let [data (tools/infrastructure-data (fixture))]
    (is (= ["0.0.0.0/0" "::/0"] (tools/cidrs data :digitalocean-http-sources)))))

(deftest dns-is-apex-and-proxied
  (let [json (tools/dns-json (assoc (fixture) :ip "192.0.2.10"))]
    (is (str/includes? json "rybbit.example.com"))
    (is (str/includes? json "192.0.2.10"))
    (is (str/includes? json "proxied"))))

(deftest inventory-keeps-one-private-target
  (let [inventory (tools/inventory (assoc (fixture) :ip "192.0.2.10"))]
    (is (str/includes? inventory "192.0.2.10"))
    (is (str/includes? inventory "rybbit-fixture"))))
