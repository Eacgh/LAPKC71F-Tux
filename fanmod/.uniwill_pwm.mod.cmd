savedcmd_uniwill_pwm.mod := printf '%s\n'   uniwill_pwm.o | awk '!x[$$0]++ { print("./"$$0) }' > uniwill_pwm.mod
