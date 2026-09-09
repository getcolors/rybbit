(ns io.github.getcolors.rybbit.validate-test
  (:require [clojure.string :as str]
            [clojure.test :refer [deftest is testing]]
            [green.cli :as green-cli]
            [io.github.getcolors.rybbit.validate :as validate]))

(def fixture-file "test/fixtures/colors.yml")
(def vultr-fixture-file "test/fixtures/colors-vultr.yml")
(def keygen-file "test/fixtures/keygen.yml")
(def keygen-vultr-file "test/fixtures/keygen-vultr.yml")
(defn read-fixture [file overrides]
  (merge (green-cli/read-state file (str/replace (slurp file) "WORKDIR" ".colors"))
         overrides))
(defn fixture
  "DigitalOcean, opt-out mode: an explicit key id and a name equal to the profile."
  [& {:as overrides}] (read-fixture fixture-file overrides))
(defn vultr-fixture
  "Vultr, opt-out mode -- the shape of the live rybbit-vultr deployment."
  [& {:as overrides}] (read-fixture vultr-fixture-file overrides))
(defn keygen
  "DigitalOcean, keygen mode: no `digitalocean-ssh-keys`, no `digitalocean-name`."
  [& {:as overrides}] (read-fixture keygen-file overrides))
(defn keygen-vultr
  "Vultr, keygen mode: no `vultr-ssh-keys`, no `vultr-name`."
  [& {:as overrides}] (read-fixture keygen-vultr-file overrides))

(deftest fixture-is-valid (is (= [] (validate/state-errors (fixture)))))
(deftest vultr-fixture-is-valid (is (= [] (validate/state-errors (vultr-fixture)))))
(deftest keygen-fixtures-are-valid
  (is (= [] (validate/state-errors (keygen))))
  (is (= [] (validate/state-errors (keygen-vultr)))))

;; --- the spec handed to ONCE

(deftest absent-machine-key-selects-keygen
  (is (validate/keygen? (keygen)))
  (is (validate/keygen? (keygen-vultr)))
  (is (not (validate/keygen? (fixture))))
  (is (not (validate/keygen? (vultr-fixture))))
  (is (validate/keygen? (vultr-fixture :vultr-ssh-keys nil)) "absence, not a flag, is the switch"))

(deftest reports-all-errors
  (let [errors (validate/state-errors
                (fixture :rybbit-host "bad" :postgres-image "floating"
                         :rybbit-backup-retention-days -1
                         :provider-dns "other" :digitalocean-vpc-uuid "forbidden"))]
    (is (<= 4 (count errors)))
    (doseq [part ["host" "image" "retention" "provider-dns"]]
      (is (some #(str/includes? % part) errors)))))

(deftest profile-overlay-is-refused
  (is (seq (validate/env-errors {"COLORS_PAR_PROFILE" "other"})))
  (is (nil? (validate/env-errors {}))))
