from plumbum import cli
from pnlpipe_lib.cli import printTable
from pnlpipe_lib.cli.params import readComboPaths

def stripKeys(dic, strs):
    def strip(s, strs):
        if not strs:
            return s
        else:
            return strip(s.replace(strs[0], ''), strs[1:])

    return {strip(k, strs): v for k, v in dic.items()}


class Status(cli.Application):
    extraFlags = cli.SwitchAttr(
        ['--extra'], help="Extra flags passed to the pipeline's status function")

    def main(self):
        combos = readComboPaths(self.parent.paramsFile)
        # print("## Parameter Combination {} ({} subjects)".format(
        #         comboPaths['id'], comboPaths['num']))

        #paramDescrips = [stripKeys(dict(p['paramCombo'],paramid=p['id']), ['hash_', 'version_']) for p in combos]
        paramDescrips = [stripKeys(
            dict(p['paramCombo']), ['hash_', 'version_']) for p in combos]
        #printTable(paramDescrips, ['paramid'] + [k for k in paramDescrips[0].keys() if k!='paramid'])
        printTable(paramDescrips)
        print
        pathCounts = []
        for combo in combos:
            d = {k: len(filter(lambda x: x.path.exists(), vs))
                 for k, vs in combo['paths'].iteritems()}
            pathCounts.append(
                dict(
                    d, paramid=combo['paramId'], numcases=combo['num']))
        cols = pathCounts[0].keys()
        cols.remove('paramid')
        cols.remove('numcases')
        printTable(pathCounts, ['paramid'] + cols + ['numcases'])

        # call pipeline's custom status
        if hasattr(self.parent, 'status'):
            # self.parent.status(concat([combo['paramPoints'] for combo in combos]))
            print
            if self.extraFlags:
                self.parent.status(combos, extraFlags=self.extraFlags.split())
            else:
                self.parent.status(combos)