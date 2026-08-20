(ns io.github.getcolors.rybbit.validate
  (:require [clojure.string :as str]
            [green.cli :as green-cli]
            [io.github.getcolors.once.validate :as once-validate]))

(def profile-par (green-cli/par-name :profile))

(def compute-providers
  "provider-compute -> what that choice implies.

  `:required` are the non-secret keys that provider's template interpolates,
  `:secrets` the credentials it needs through COLORS_PAR_*, and `:tofu-env` the
  subset OpenTofu reads from the process environment itself. Keeping the three
  together is what stops a provider being validated against one set of keys and
  run with another -- a stage exporting a credential nobody checked for, or a
  check demanding a key no template uses.

  Both providers need firewall sources because this package puts a provider
  firewall in front of the host; ONCE's compute templates have none, so its
  registry entries are shorter."
  {"digitalocean"
   {:required [:digitalocean-name :digitalocean-region :digitalocean-size
               :digitalocean-image :digitalocean-ssh-keys
               :digitalocean-ssh-sources :digitalocean-http-sources]
    :secrets [:do-token]
    :tofu-env {:do-token "DIGITALOCEAN_TOKEN"}}
   "vultr"
   {:required [:vultr-name :vultr-region :vultr-plan :vultr-os-id
               :vultr-ssh-keys :vultr-ssh-sources :vultr-http-sources]
    :secrets [:vultr-api-key]
    :tofu-env {:vultr-api-key "VULTR_API_KEY"}}})

(def required
  [:profile :workdir :provider-compute :provider-dns :provider-backend
   :compute-prevent-destroy :rybbit-host :rybbit-disable-signup
   :postgres-image :clickhouse-image :redis-image
   :rybbit-backend-image :rybbit-client-image :caddy-image
   :postgres-data-dir :clickhouse-data-dir :redis-data-dir :rybbit-backup-dir
   :rybbit-backup-r2-bucket :rybbit-backup-r2-endpoint
   :rybbit-backup-r2-region :rybbit-backup-oncalendar
   :rybbit-backup-retention-days
   :r2-bucket :r2-endpoint])
(def host-re #"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")
(def image-re
  ;; name:tag, name@sha256:..., or name:tag@sha256:... A digest is the only
  ;; pin that cannot move under the deployment, so validation must accept it.
  #"^[^\s:@]+(?:/[^\s:@]+)*(?::[^\s:@]+|(?::[^\s:@]+)?@sha256:[0-9a-f]{64})$")
(defn missing? [x] (or (nil? x) (and (string? x) (str/blank? x))))
(defn env-errors [env]
  (when (not-empty (str (get env profile-par)))
    [(str profile-par " is set; profile must come from colors.yml only")]))

(defn compute-provider [opts] (get compute-providers (:provider-compute opts)))

(defn state-errors [opts]
  (vec
   (concat
    (for [k (concat required (:required (compute-provider opts)))
          :when (missing? (get opts k))]
      (str k " is required"))
    (when-not (compute-provider opts)
      [(str ":provider-compute must be one of "
            (str/join ", " (sort (keys compute-providers))))])
    (when-not (= "cloudflare" (:provider-dns opts))
      [":provider-dns must be cloudflare"])
    (when-not (contains? #{"local" "s3" "r2"} (:provider-backend opts))
      [":provider-backend must be local, s3, or r2"])
    (when-not (boolean? (:compute-prevent-destroy opts))
      [":compute-prevent-destroy must be true or false"])
    (when-not (or (missing? (:rybbit-host opts))
                  (re-matches host-re (str (:rybbit-host opts))))
      [":rybbit-host must be a fully qualified hostname"])
    (for [k [:postgres-image :clickhouse-image :redis-image
             :rybbit-backend-image :rybbit-client-image :caddy-image]
          :let [v (get opts k)]
          :when (and (not (missing? v)) (not (re-matches image-re (str v))))]
      (str k " must carry an explicit image tag"))
    (for [k [:rybbit-backup-retention-days]
          :when (and (not (missing? (get opts k)))
                     (not (and (integer? (get opts k)) (pos? (get opts k)))))]
      (str k " must be a positive integer"))
    (when (contains? opts :digitalocean-vpc-uuid)
      [":digitalocean-vpc-uuid must be absent; the default regional VPC is discovered at runtime"])
    (when (contains? opts :digitalocean-vpc-cidr)
      [":digitalocean-vpc-cidr must be absent; this package must not create a VPC"]))))

(defn backend-secrets [opts]
  (:secrets (get-in once-validate/providers
                    [:provider-backend (:provider-backend opts)])))
(defn secret-errors [opts]
  (let [keys (concat (:secrets (compute-provider opts))
                     [:cloudflare-api-token
                      :rybbit-backup-r2-access-key-id
                      :rybbit-backup-r2-secret-access-key]
                     (backend-secrets opts))]
    (for [k (distinct keys) :when (missing? (get opts k))]
      (str "required credential is not set: " (green-cli/par-name k)))))

(defn tofu-env [opts slot]
  (case slot
    :provider-compute (:tofu-env (compute-provider opts) {})
    :provider-dns {:cloudflare-api-token "CLOUDFLARE_API_TOKEN"}
    :provider-backend (:tofu-env (get-in once-validate/providers
                                         [:provider-backend (:provider-backend opts)]) {})
    {}))
