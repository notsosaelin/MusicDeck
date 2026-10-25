/* What compat.h declares: as POSIX would answer on a system with no signals to block, no locks to take,
   no symlinks and no root. */
#include <errno.h>
#include <fcntl.h>
#include <io.h>
#include <process.h>
#include <signal.h>
#include "compat.h"

int sigaction(int signum, const struct sigaction *act, struct sigaction *oldact)
{
	void (*old)(int) = SIG_DFL;
	if (act && signum > 0 && signum < NSIG && signum != SIGKILL && signum != SIGSTOP)
		old = signal(signum, act->sa_handler);
	if (oldact) {
		oldact->sa_handler = old == SIG_ERR ? SIG_DFL : old;
		oldact->sa_mask = 0;
		oldact->sa_flags = 0;
	}
	return 0;
}

int sigfillset(sigset_t *set) { *set = ~0UL; return 0; }
int sigemptyset(sigset_t *set) { *set = 0; return 0; }
int sigdelset(sigset_t *set, int signum) { *set &= ~(1UL << (signum & 31)); return 0; }
int sigaddset(sigset_t *set, int signum) { *set |= 1UL << (signum & 31); return 0; }

int fcntl(int fd, int cmd, ...)
{
	(void)fd;
	return cmd == F_GETFL ? O_RDWR : 0;
}

int kill(int pid, int sig)
{
	if (pid == _getpid())
		return raise(sig);
	errno = EPERM;
	return -1;
}

unsigned int alarm(unsigned int seconds) { (void)seconds; return 0; }
int pipe(int fds[2]) { return _pipe(fds, 65536, _O_BINARY); }
int fsync(int fd) { return _commit(fd); }

int readlink(const char *path, char *buf, size_t size)
{
	(void)path; (void)buf; (void)size;
	errno = EINVAL;  /* not a symlink */
	return -1;
}

unsigned int getuid(void) { return 1000; }
unsigned int geteuid(void) { return 1000; }
