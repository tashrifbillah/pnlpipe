from plumbum import cli

class PipelineSubcommand(cli.Application):
    def main(self, *args):
        if args:
            print("Unknown command {0!r}".format(args[0]))
            return 1
        if not self.nested_command:
            print("No command given")
            return 1  # error exit code