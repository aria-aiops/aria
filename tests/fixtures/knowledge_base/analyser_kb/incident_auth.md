# Auth Failure — Example Log
label: incident | type: auth

2024-01-17 09:45:33 ERROR KerberosAuthenticator: AuthenticationException: SASL authentication failed for user hive
2024-01-17 09:45:33 FATAL HiveServer2: Unable to obtain Kerberos TGT — ticket expired or missing keytab
2024-01-17 09:45:34 WARN  HiveMetaStore: Connection refused — authentication rejected for principal aria-svc@REALM.COM
2024-01-17 09:45:35 ERROR ZooKeeperClient: Session expired — Kerberos credentials invalid
