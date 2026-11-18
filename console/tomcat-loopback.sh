#!/usr/bin/env bash
# Sourced by the Guacamole entrypoint after it creates the Tomcat configuration.
sed -i 's/<Connector port="8080" protocol="HTTP\/1.1"/<Connector address="127.0.0.1" port="8788" protocol="HTTP\/1.1"/' \
    "${CATALINA_BASE:?}/conf/server.xml"
grep -Fq '<Connector address="127.0.0.1" port="8788" protocol="HTTP/1.1"' \
    "$CATALINA_BASE/conf/server.xml"
# Disable the separate Tomcat shutdown socket on the host network.
sed -i 's/<Server port="8005"/<Server port="-1"/' "$CATALINA_BASE/conf/server.xml"
grep -Fq '<Server port="-1"' "$CATALINA_BASE/conf/server.xml"
