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

(deftest the-spec-carries-this-packages-registry-sources-and-default
  ;; The operations are ONCE's; this is the data they run over. A colour
  ;; whose registry, sources or default drifts fails here, in that colour.
  (is (= #{"digitalocean" "vultr"} (set (keys (:registry validate/spec)))))
  (is (= validate/compute-providers (:registry validate/spec)))
  (is (= {:required [:digitalocean-region :digitalocean-size :digitalocean-image
                     :digitalocean-ssh-sources :digitalocean-http-sources]
          :secrets [:do-token]
          :tofu-env {:do-token "DIGITALOCEAN_TOKEN"}}
         (get-in validate/spec [:registry "digitalocean"])))
  (is (= {:required [:vultr-region :vultr-plan :vultr-os-id
                     :vultr-ssh-sources :vultr-http-sources]
          :secrets [:vultr-api-key]
          :tofu-env {:vultr-api-key "VULTR_API_KEY"}}
         (get-in validate/spec [:registry "vultr"])))
  (is (= {:non-empty ["ssh-sources"] :may-be-empty ["http-sources"]} (:sources validate/spec)))
  ;; Vultr, not DigitalOcean: the default is what a legacy state without
  ;; params.provider is, and the only legacy state is the live Vultr deployment.
  (is (= "vultr" (:default validate/spec)))
  (is (= validate/default-compute-provider (:default validate/spec)))
  (is (not (contains? validate/spec :name-rules)) "the name rules are ONCE's"))

;; --- the compute-provider registry

(deftest compute-provider-must-be-one-the-package-has-a-template-for
  ;; The registry is the only list; a provider accepted here with no template
  ;; directory would fail at render time instead of at validation.
  (let [errors (validate/state-errors (fixture :provider-compute "hcloud"))]
    (is (some #{":provider-compute must be one of digitalocean, vultr"} errors))))

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

(deftest name-and-machine-key-are-never-required
  ;; `<provider>-name` is an optional override of the profile and
  ;; `<provider>-ssh-keys` is meaningful by its absence, so neither may be in
  ;; the registry's required list -- a required machine key would make keygen
  ;; mode unreachable.
  (doseq [entry (vals validate/compute-providers) k (:required entry)]
    (is (not (str/ends-with? (name k) "-name")) (str k))
    (is (not (str/ends-with? (name k) "-ssh-keys")) (str k)))
  (is (= [] (validate/state-errors (fixture :digitalocean-name nil :digitalocean-ssh-keys nil))))
  (is (= [] (validate/state-errors (vultr-fixture :vultr-name nil :vultr-ssh-keys nil)))))

(deftest absent-machine-key-selects-keygen
  (is (validate/keygen? (keygen)))
  (is (validate/keygen? (keygen-vultr)))
  (is (not (validate/keygen? (fixture))))
  (is (not (validate/keygen? (vultr-fixture))))
  (is (validate/keygen? (vultr-fixture :vultr-ssh-keys nil)) "absence, not a flag, is the switch"))

(deftest compute-name-falls-back-to-the-profile
  (is (= "rybbit-fixture" (validate/compute-name (fixture))))
  (is (= "rybbit-keygen-fixture" (validate/compute-name (keygen))))
  (is (= "custom" (validate/compute-name (vultr-fixture :vultr-name "custom"))))
  (is (= :vultr-ssh-sources (validate/compute-key (vultr-fixture) "ssh-sources"))))

(deftest compute-credentials-follow-the-provider
  (is (= {:do-token "DIGITALOCEAN_TOKEN"} (validate/tofu-env (fixture) :provider-compute)))
  (is (= {:vultr-api-key "VULTR_API_KEY"} (validate/tofu-env (vultr-fixture) :provider-compute)))
  ;; And the credential checked is the credential exported: a Vultr deployment
  ;; must not be asked for a DigitalOcean token it never uses.
  (let [errors (str/join "\n" (validate/secret-errors (vultr-fixture)))]
    (is (str/includes? errors "COLORS_PAR_VULTR_API_KEY"))
    (is (not (str/includes? errors "COLORS_PAR_DO_TOKEN")))))

;; --- the network contract, wired through state-errors with ONCE's messages

(deftest ssh-sources-must-not-be-empty
  ;; A machine nobody can reach is not a deployment; an empty HTTP list is
  ;; simply no public HTTP.
  (is (some #{":vultr-ssh-sources must list at least one CIDR"}
            (validate/state-errors (vultr-fixture :vultr-ssh-sources []))))
  (is (some #{":digitalocean-ssh-sources must list at least one CIDR"}
            (validate/state-errors (fixture :digitalocean-ssh-sources []))))
  (is (= [] (validate/state-errors (vultr-fixture :vultr-http-sources []))))
  (is (= [] (validate/state-errors (fixture :digitalocean-http-sources [])))))

(deftest malformed-sources-are-refused-before-any-provider-call
  (is (some #{":vultr-http-sources entry \"203.0.113.0\" is not an IPv4 or IPv6 CIDR"}
            (validate/state-errors (vultr-fixture :vultr-http-sources ["203.0.113.0"]))))
  (is (some #{":digitalocean-ssh-sources entry \"nope\" is not an IPv4 or IPv6 CIDR"}
            (validate/state-errors (fixture :digitalocean-ssh-sources ["0.0.0.0/0" "nope"]))))
  (is (= [] (validate/state-errors (fixture :digitalocean-ssh-sources ["2001:db8::/32" "203.0.113.4/32"])))))

;; --- provider checks run only for the selected provider

(deftest provider-checks-are-scoped-to-the-selected-provider
  (testing "DigitalOcean's VPC keys are refused on DigitalOcean"
    (is (some #(str/includes? % "must be absent")
              (validate/state-errors (fixture :digitalocean-vpc-cidr "10.0.0.0/16"))))
    (is (some #(str/includes? % "vpc-uuid")
              (validate/state-errors (fixture :digitalocean-vpc-uuid "forbidden")))))
  (testing "and ignored on Vultr, like every other unselected provider's key"
    (is (= [] (validate/state-errors (vultr-fixture :digitalocean-vpc-uuid "ignored"
                                                    :digitalocean-vpc-cidr "10.0.0.0/16")))))
  (testing "Vultr's os-id is numeric, and only checked on Vultr"
    (is (some #{":vultr-os-id must be Vultr's numeric operating-system id"}
              (validate/state-errors (vultr-fixture :vultr-os-id "ubuntu"))))
    (is (= [] (validate/state-errors (fixture :vultr-os-id "ubuntu"))))))

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
