(ns io.github.getcolors.rybbit.workflow-test
  (:require [babashka.fs :as fs]
            [clojure.string :as str]
            [clojure.test :refer [deftest is testing]]
            [io.github.getcolors.rybbit.validate-test :refer [fixture vultr-fixture keygen keygen-vultr]]
            [io.github.getcolors.rybbit.workflow :as workflow]))

;; The compute state is read once per run, through `state-output`, on a real
;; create or delete. Every lifecycle test stubs it: nil is a readable state
;; holding no compute, a map is a recorded `params`, and a throw is a backend
;; that cannot be read.
(defn- start [opts state]
  (with-redefs [workflow/state-output (fn [_] state)]
    (workflow/start-step opts {})))

(defn- start-unreadable [opts]
  ;; The shape `green.tofu/outputs` throws: an ex-info carrying `:dir`. Only
  ;; that is an unreadable backend; anything else propagates as a defect.
  (with-redefs [workflow/state-output (fn [_] (throw (ex-info "tofu output failed: no backend" {:dir "x"})))]
    (workflow/start-step opts {})))

(def credentials
  {:vultr-api-key "v" :do-token "d" :cloudflare-api-token "c"
   :r2-access-key-id "a" :r2-secret-access-key "s"
   :rybbit-backup-r2-access-key-id "k" :rybbit-backup-r2-secret-access-key "s"})

(deftest build-and-dry-run-need-no-credentials
  (is (= 0 (:green/exit (workflow/start-step (assoc (fixture) :green/event :build) {}))))
  (is (= 0 (:green/exit (workflow/start-step
                         (assoc (fixture) :green/event :create :green/dry-run true) {}))))
  (is (= 0 (:green/exit (workflow/start-step (assoc (vultr-fixture) :green/event :build) {})))))

(deftest build-and-dry-run-never-touch-ssh-or-state
  ;; The standard forbids reading, creating, or requiring anything under ~/.ssh
  ;; on a build or dry-run: they render from desired state alone. Nor do they
  ;; read the backend: a throwing state read proves nothing on these paths
  ;; reaches it.
  (doseq [opts [(assoc (keygen) :green/event :build)
                (assoc (keygen-vultr) :green/event :create :green/dry-run true)
                (assoc (keygen) :green/event :delete :green/dry-run true)]]
    (let [result (start-unreadable opts)]
      (is (= 0 (:green/exit result)))
      (is (str/starts-with? (str (:ssh-public-key-path result)) "/home/build-placeholder")
          "a build must not name the operator's home directory"))))

(deftest real-create-requires-credentials
  (let [r (start (assoc (fixture) :green/event :create) nil)]
    (is (= 2 (:green/exit r)))
    (is (str/includes? (:green/err r) "COLORS_PAR_DO_TOKEN"))
    (is (str/includes? (:green/err r) "COLORS_PAR_RYBBIT_BACKUP_R2_SECRET_ACCESS_KEY"))))

(deftest real-create-and-delete-require-the-selected-providers-credentials
  (testing "create on Vultr"
    (let [r (start (assoc (vultr-fixture) :green/event :create) nil)]
      (is (= 2 (:green/exit r)))
      (is (str/includes? (:green/err r) "COLORS_PAR_VULTR_API_KEY"))
      (is (not (str/includes? (:green/err r) "COLORS_PAR_DO_TOKEN")))))
  (testing "delete on Vultr"
    (let [r (start (assoc (vultr-fixture) :green/event :delete :compute-prevent-destroy false) nil)]
      (is (= 2 (:green/exit r)))
      (is (str/includes? (:green/err r) "COLORS_PAR_VULTR_API_KEY"))
      (is (not (str/includes? (:green/err r) "COLORS_PAR_DO_TOKEN")))))
  (testing "delete on DigitalOcean"
    (let [r (start (assoc (fixture) :green/event :delete :compute-prevent-destroy false) nil)]
      (is (= 2 (:green/exit r)))
      (is (str/includes? (:green/err r) "COLORS_PAR_DO_TOKEN"))
      (is (not (str/includes? (:green/err r) "COLORS_PAR_VULTR_API_KEY"))))))

(deftest delete-is-protected
  (let [r (start (assoc (fixture) :green/event :delete) nil)]
    (is (= 2 (:green/exit r)))
    (is (str/includes? (:green/err r) "COMPUTE_PREVENT_DESTROY"))))

;; --- provider switching is a rebuild, never an apply

(deftest a-provider-switch-is-refused-on-create-and-delete
  (doseq [event [:create :delete]]
    (testing (str "Vultr selected, DigitalOcean recorded, on " (name event))
      (let [r (start (assoc (vultr-fixture) :green/event event :compute-prevent-destroy false)
                     {:provider "digitalocean" :ip "203.0.113.9"})]
        (is (= 2 (:green/exit r)))
        (is (str/includes? (:green/err r)
                           "state holds a digitalocean machine; set provider-compute back to digitalocean and delete first"))
        ;; The validator order is the thing under test: the actionable error,
        ;; not a missing token for the provider that was just selected.
        (is (not (str/includes? (:green/err r) "required credential is not set")))))
    (testing (str "DigitalOcean selected, Vultr recorded, on " (name event))
      (let [r (start (assoc (fixture) :green/event event :compute-prevent-destroy false)
                     {:provider "vultr" :ip "203.0.113.9"})]
        (is (= 2 (:green/exit r)))
        (is (str/includes? (:green/err r) "state holds a vultr machine; set provider-compute back to vultr and delete first"))
        (is (not (str/includes? (:green/err r) "COLORS_PAR_DO_TOKEN")))))))

(deftest legacy-state-accepts-only-the-default-provider
  ;; A state recorded before this package wrote params.provider is the live
  ;; Vultr deployment's: accepted on Vultr, refused on DigitalOcean.
  (doseq [event [:create :delete]]
    (let [r (start (assoc (vultr-fixture) :green/event event :compute-prevent-destroy false)
                   {:ip "203.0.113.9"})]
      (is (not (str/includes? (:green/err r) "state holds")) (name event))
      (is (str/includes? (:green/err r) "required credential is not set") (name event)))
    (let [r (start (assoc (fixture) :green/event event :compute-prevent-destroy false)
                   {:ip "203.0.113.9"})]
      (is (= 2 (:green/exit r)))
      (is (str/includes? (:green/err r) "no recorded provider") (name event))
      (is (str/includes? (:green/err r) "set provider-compute back to vultr and delete first"))
      (is (not (str/includes? (:green/err r) "COLORS_PAR_DO_TOKEN"))))))

(deftest a-matching-provider-passes-to-the-credentials
  (let [r (start (assoc (fixture) :green/event :create) {:provider "digitalocean" :ip "203.0.113.9"})]
    (is (= 2 (:green/exit r)))
    (is (not (str/includes? (:green/err r) "state holds")))
    (is (str/includes? (:green/err r) "COLORS_PAR_DO_TOKEN"))))

(deftest an-unreadable-backend-counts-as-no-state-on-create
  ;; A fresh clone has no readable state and must still be able to create.
  (let [r (start-unreadable (assoc (vultr-fixture) :green/event :create))]
    (is (= 2 (:green/exit r)))
    (is (not (str/includes? (:green/err r) "could not read")))
    (is (not (str/includes? (:green/err r) "state holds")))
    (is (str/includes? (:green/err r) "COLORS_PAR_VULTR_API_KEY"))))

(deftest a-real-create-on-a-fresh-work-directory-reports-the-credentials-not-a-crash
  ;; No state stub: the real `state-output` runs against a work directory
  ;; that holds no stage yet, as a fresh clone's does. Green's SDK shells out
  ;; to tofu in a directory that does not exist and reports that launch
  ;; failure itself as its `tofu output failed:` step error, which ONCE's
  ;; `read-state` counts as an unreadable state, so the create reports its
  ;; credentials instead of crashing.
  (let [work (str (fs/create-temp-dir {:prefix "rybbit-fresh"}))]
    (try
      (let [r (workflow/start-step (assoc (vultr-fixture) :workdir work :green/event :create) {})]
        (is (= 2 (:green/exit r)))
        (is (str/includes? (str (:green/err r)) "COLORS_PAR_VULTR_API_KEY"))
        (is (not (str/includes? (str (:green/err r)) "could not read"))))
      (finally (fs/delete-tree work)))))

(defn deletable-fixture
  "A fixture that passes real-delete preflight: guard lifted, secrets present."
  [& {:as overrides}]
  (merge (fixture :compute-prevent-destroy false) credentials overrides))

(deftest delete-fails-loudly-when-state-is-unreadable
  ;; Swallowing a failed state read is how a live teardown ended up pointing
  ;; the cleanup playbook at 192.0.2.10: stale backend credentials made
  ;; `tofu output` fail, nil was merged, and the inventory fell back to
  ;; TEST-NET. The failure must surface here, before any playbook runs, with
  ;; the standard's wording.
  (with-redefs [workflow/state-output (fn [_] (throw (ex-info "Unauthorized" {:dir "x"})))]
    (let [r (workflow/start-step (deletable-fixture :green/event :delete) {})]
      (is (= 1 (:green/exit r)))
      (is (str/includes? (:green/err r) "could not read the infrastructure state for the delete cleanup"))
      (is (str/includes? (:green/err r) "Unauthorized")))))

(deftest delete-with-explicit-ip-overrides-the-adopted-address-after-the-read
  ;; COLORS_PAR_IP replaces a stale recorded address; it never skips the read
  ;; or the provider guard. On a readable state the override wins over the
  ;; recorded address; an unreadable backend still fails closed with it set.
  (let [r (start (deletable-fixture :green/event :delete :ip "203.0.113.7")
                 {:provider "digitalocean" :ip "198.51.100.1" :user "root"})]
    (is (= 0 (:green/exit r)))
    (is (= "203.0.113.7" (:ip r))))
  (let [r (start-unreadable (deletable-fixture :green/event :delete :ip "203.0.113.7"))]
    (is (= 1 (:green/exit r)))
    (is (str/includes? (:green/err r) "could not read the infrastructure state for the delete cleanup"))))

(deftest delete-with-empty-state-proceeds-without-an-address
  ;; State readable, no compute recorded: the instance is already gone, the
  ;; cleanup step skips itself, and the rest of the teardown still runs.
  (let [r (start (deletable-fixture :green/event :delete) nil)]
    (is (= 0 (:green/exit r)))
    (is (nil? (:ip r)))))

(deftest a-real-delete-adopts-the-recorded-address
  (let [r (start (merge (vultr-fixture) credentials {:green/event :delete :compute-prevent-destroy false})
                 {:provider "vultr" :ip "203.0.113.9" :user "root"})]
    (is (= 0 (:green/exit r)))
    (is (= "203.0.113.9" (:ip r)))))

(deftest graph-orders-private-stack
  (is (= [:rybbit/infrastructure]
         (vec (rest (workflow/wire-fn :rybbit/start {:green/event :create})))))
  (is (= [:rybbit/dns]
         (vec (rest (workflow/wire-fn :rybbit/infrastructure {:green/event :create})))))
  (is (= [:rybbit/ansible]
         (vec (rest (workflow/wire-fn :rybbit/start {:green/event :delete}))))))

(deftest delete-removes-the-key-after-the-compute-destroy
  ;; The ordering is what makes "key present <=> deployment exists" hold: a
  ;; failed destroy never reaches the cleanup step, and correctly leaves the
  ;; key that is still the only credential to whatever survived.
  (is (= [:rybbit/ssh-cleanup]
         (vec (rest (workflow/wire-fn :rybbit/infrastructure {:green/event :delete})))))
  (is (empty? (rest (workflow/wire-fn :rybbit/ssh-cleanup {:green/event :delete}))))
  (is (some #{:rybbit/ssh-cleanup} workflow/side-effecting) "a dry-run delete touches no key"))
