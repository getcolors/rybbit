(ns io.github.getcolors.rybbit.ssh
  "Explicit public identity selection within a temporary agent scope."
  (:require [io.github.getcolors.rybbit.access :as access]
            [io.github.getcolors.rybbit.compute :as compute]))
(def rendered-only? compute/planning?)
(def identity-args access/identity-args)
(defn with-machine-key [opts] opts)
(defn private-key-path [opts]
  (or (:ssh-private-key-path opts) (throw (ex-info "deployment SSH identity unavailable" {}))))
