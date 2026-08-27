(ns io.github.getcolors.rybbit.validate-test
  (:require [clojure.string :as str]
            [clojure.test :refer [deftest is]]
            [green.cli :as green-cli]
            [io.github.getcolors.rybbit.validate :as validate]))

(def fixture-file "test/fixtures/colors.yml")
(def vultr-fixture-file "test/fixtures/colors-vultr.yml")
(defn read-fixture [file overrides]
  (merge (green-cli/read-state file (str/replace (slurp file) "WORKDIR" ".colors"))
         overrides))
(defn fixture [& {:as overrides}] (read-fixture fixture-file overrides))
(defn vultr-fixture [& {:as overrides}] (read-fixture vultr-fixture-file overrides))

(deftest fixture-is-valid (is (= [] (validate/state-errors (fixture)))))
(deftest vultr-fixture-is-valid (is (= [] (validate/state-errors (vultr-fixture)))))

(deftest compute-provider-must-be-one-the-package-has-a-template-for
  ;; The registry is the only list; a provider accepted here with no template
  ;; directory would fail at render time instead of at validation.
  (let [errors (validate/state-errors (fixture :provider-compute "hcloud"))]
    (is (some #(str/includes? % "digitalocean, vultr") errors))))

(deftest each-provider-requires-only-its-own-keys
  ;; The DigitalOcean keys are not required of a Vultr deployment, and vice
  ;; versa -- a flat required list made every deployment carry both.
  (is (some #(str/includes? % "vultr-plan")
            (validate/state-errors (vultr-fixture :vultr-plan nil))))
  (is (empty? (filter #(str/includes? % "digitalocean")
                      (validate/state-errors (vultr-fixture)))))
  (is (some #(str/includes? % "digitalocean-size")
            (validate/state-errors (fixture :digitalocean-size nil))))
  (is (empty? (filter #(str/includes? % "vultr")
                      (validate/state-errors (fixture))))))

(deftest compute-credentials-follow-the-provider
  (is (= {:do-token "DIGITALOCEAN_TOKEN"} (validate/tofu-env (fixture) :provider-compute)))
  (is (= {:vultr-api-key "VULTR_API_KEY"} (validate/tofu-env (vultr-fixture) :provider-compute)))
  ;; And the credential checked is the credential exported: a Vultr deployment
  ;; must not be asked for a DigitalOcean token it never uses.
  (let [errors (str/join "\n" (validate/secret-errors (vultr-fixture)))]
    (is (str/includes? errors "COLORS_PAR_VULTR_API_KEY"))
    (is (not (str/includes? errors "COLORS_PAR_DO_TOKEN")))))

(deftest reports-all-errors
  (let [errors (validate/state-errors
                (fixture :rybbit-host "bad" :postgres-image "floating"
                         :rybbit-backup-retention-days -1
                         :provider-dns "other" :digitalocean-vpc-uuid "forbidden"))]
    (is (<= 5 (count errors)))
    (doseq [part ["host" "image" "retention" "provider-dns" "vpc-uuid"]]
      (is (some #(str/includes? % part) errors)))))

(deftest forbids-vpc-configuration
  (is (some #(str/includes? % "must be absent")
            (validate/state-errors (fixture :digitalocean-vpc-cidr "10.0.0.0/16")))))

(deftest profile-overlay-is-refused
  (is (seq (validate/env-errors {"COLORS_PAR_PROFILE" "other"})))
  (is (nil? (validate/env-errors {}))))

(deftest names-all-package-secrets
  (let [errors (str/join "\n" (validate/secret-errors (fixture)))]
    (doseq [name ["COLORS_PAR_DO_TOKEN" "COLORS_PAR_CLOUDFLARE_API_TOKEN"
                  "COLORS_PAR_R2_ACCESS_KEY_ID" "COLORS_PAR_R2_SECRET_ACCESS_KEY"
                  "COLORS_PAR_RYBBIT_BACKUP_R2_ACCESS_KEY_ID"
                  "COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY"]]
      (is (str/includes? errors name)))))
