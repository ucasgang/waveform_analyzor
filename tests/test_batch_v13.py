import copy,io,json,struct,tempfile,time,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from esswave.batchwork import BatchManager,plan_files,safe_relative,strict_json,csv_summary,batch_index
from esswave.generate import scenario,demo_suite

class FolderPlanTests(unittest.TestCase):
    def files(self,*paths):return [{'path':p,'size':12} for p in paths]
    def test_recursive_same_stem_different_folders(self):
        p=plan_files(self.files('根/A/x.cfg','根/A/x.dat','根/A/x.json','根/B/x.CFG','根/B/x.DAT','根/B/x.JSON'))
        self.assertEqual(len(p['rows']),2);self.assertTrue(all(r['status']=='WAITING' for r in p['rows']))
    def test_no_cross_directory_pairing(self):
        p=plan_files(self.files('a/x.cfg','b/x.dat'),'common');self.assertEqual(len(p['rows']),2);self.assertTrue(all(r['status']=='ERROR' for r in p['rows']))
    def test_orphan_dat_is_error(self):self.assertEqual(plan_files(self.files('x.dat'),'common')['rows'][0]['status'],'ERROR')
    def test_missing_json_is_error(self):self.assertEqual(plan_files(self.files('x.cfg','x.dat'))['rows'][0]['status'],'ERROR')
    def test_common_config_needs_no_sibling(self):self.assertEqual(plan_files(self.files('x.cfg','x.dat'),'common')['rows'][0]['status'],'WAITING')
    def test_case_collisions_not_guessed(self):self.assertEqual(plan_files(self.files('x.cfg','X.CFG','x.dat'),'common')['rows'][0]['status'],'ERROR')
    def test_duplicate_dat_not_guessed(self):self.assertEqual(plan_files(self.files('x.cfg','x.dat','x.dat'),'common')['rows'][0]['status'],'ERROR')
    def test_duplicate_case_not_guessed(self):self.assertEqual(plan_files(self.files('x.cfg','x.dat','x.json','x.JSON'))['rows'][0]['status'],'ERROR')
    def test_empty_and_oversize_items(self):
        for size in (0,129*1024**2):
            p=plan_files([{'path':'x.cfg','size':10},{'path':'x.dat','size':size}],'common');self.assertEqual(p['rows'][0]['status'],'ERROR')
    def test_unicode_paths_preserved(self):self.assertEqual(plan_files(self.files('储能/一期甲.cfg','储能/一期甲.dat'),'common')['rows'][0]['cfg'],'储能/一期甲.cfg')
    def test_extra_metadata_does_not_create_fake_case(self):self.assertEqual(len(plan_files(self.files('x.cfg','x.dat','x.json','x.truth.json','manifest.json'))['rows']),1)
    def test_no_cfg_no_pass(self):
        with self.assertRaises(ValueError):plan_files(self.files('manifest.json'))
    def test_recursive_off_does_not_mix_descendants(self):
        p=plan_files(self.files('root/x.cfg','root/x.dat','root/sub/y.cfg','root/sub/y.dat'),'common',False);self.assertEqual(len(p['rows']),1)
    def test_total_size_cap(self):
        with self.assertRaises(ValueError):plan_files([{'path':'x.cfg','size':2*1024**3+1}])
    def test_json_duplicate_keys(self):
        with self.assertRaises(ValueError):strict_json('{"x":1,"x":2}')
    def test_json_nonfinite(self):
        for value in ('NaN','Infinity','-Infinity'):
            with self.assertRaises(ValueError):strict_json('{"x":'+value+'}')
    def test_cli_plan_does_not_inherit_browser_upload_cap(self):
        p=plan_files([{'path':'x.cfg','size':1000},{'path':'x.dat','size':256*1024**2}],'common',browser_limits=False)
        self.assertEqual(p['rows'][0]['status'],'WAITING')
    def test_json_utf8_bom(self):self.assertEqual(strict_json(b'\xef\xbb\xbf{"x":1}'),{'x':1})

for bad_i,bad in enumerate(('../x.cfg','a/../x.cfg','/tmp/x.cfg','C:/x.cfg','a\\x.cfg','a//x.cfg','a/./x.cfg','a/\x00.cfg','a/\n.cfg','')):
    def check(self,bad=bad):
        with self.assertRaises(ValueError):safe_relative(bad)
    setattr(FolderPlanTests,'test_path_'+str(bad_i),check)

class BatchJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.manager=BatchManager(self.root/'jobs')
        self.cfg,self.case=scenario(self.root/'source','one');self.config=json.loads(self.case.read_text());self.dat=self.cfg.with_suffix('.dat')
    def tearDown(self):self.manager.close();self.tmp.cleanup()
    def make(self,mode='sibling',names=('a',),config=None):
        files=[]
        for name in names:
            files+=[{'path':name+'.cfg','size':self.cfg.stat().st_size},{'path':name+'.dat','size':self.dat.stat().st_size}]
            if mode=='sibling':files.append({'path':name+'.json','size':self.case.stat().st_size})
        return self.manager.create(files,mode,config or self.config)
    def upload(self,snapshot,row=None,case=None,dat=None):
        row=row or snapshot['rows'][0];c=self.cfg.read_bytes();d=self.dat.read_bytes() if dat is None else dat
        j=(self.case.read_bytes() if case is None else case) if row['case'] else b''
        return self.manager.upload(snapshot['id'],row['id'],struct.pack('<II',len(c),len(j))+c+d+j)
    def wait(self,ident):
        for _ in range(400):
            s=self.manager.snapshot(ident)
            if s['state'] in ('COMPLETE','CANCELLED'):return s
            time.sleep(.01)
        self.fail('worker did not finish')
    def test_common_snapshot_is_immutable(self):
        c=copy.deepcopy(self.config);s=self.make('common',config=c);c['tests'][0]['count']=999;self.upload(s);self.manager.start(s['id']);done=self.wait(s['id']);self.assertEqual(done['verdict'],'PASS')
    def test_sibling_roundtrip_export_and_open(self):
        s=self.make();self.upload(s);self.manager.start(s['id']);done=self.wait(s['id']);self.assertEqual(done['verdict'],'PASS')
        rec,res=self.manager.open_result(s['id'],s['rows'][0]['id']);self.assertEqual(res['status'],'PASS');self.assertEqual(rec.hashes,res['record']['hashes'])
        p=self.manager.export_zip(s['id'])
        with zipfile.ZipFile(p) as z:
            self.assertIn('index.html',z.namelist());self.assertIn('summary.csv',z.namelist());self.assertFalse(any(n.endswith('.dat') for n in z.namelist()))
            h=z.read('items/r00001/out/report.html').decode();self.assertIn('wave-report-data',h);self.assertIn('"resolution":"full"',h)
    def test_staging_never_pass(self):self.assertEqual(self.make()['verdict'],'INCONCLUSIVE')
    def test_cancel_unstarted_preserves_unexecuted(self):
        s=self.make();done=self.manager.cancel(s['id']);self.assertEqual(done['state'],'CANCELLED');self.assertEqual(done['counts']['CANCELLED'],1);self.assertNotEqual(done['verdict'],'PASS')
    def test_cannot_start_before_upload(self):
        with self.assertRaises(ValueError):self.manager.start(self.make()['id'])
    def test_cannot_export_running_or_staging(self):
        with self.assertRaises(ValueError):self.manager.export_zip(self.make()['id'])
    def test_concurrent_active_jobs_rejected(self):
        self.make()
        with self.assertRaises(ValueError):self.make()
    def test_duplicate_upload_rejected(self):
        s=self.make();self.upload(s)
        with self.assertRaises(ValueError):self.upload(s)
    def test_upload_length_mismatch_becomes_error(self):
        s=self.make()
        with self.assertRaises(ValueError):self.upload(s,dat=self.dat.read_bytes()[:-1])
        self.assertEqual(self.manager.snapshot(s['id'])['counts']['ERROR'],1)
    def test_bad_config_does_not_become_pass(self):
        s=self.make();bad=b' '*self.case.stat().st_size
        with self.assertRaises(ValueError):self.upload(s,case=bad)
        self.manager.start(s['id']);self.assertEqual(self.wait(s['id'])['verdict'],'ERROR')
    def test_bad_dat_does_not_stop_later_file(self):
        s=self.make(names=('bad','good'));bad=bytearray(self.dat.read_bytes());bad[:4]=struct.pack('<I',99)
        self.upload(s,s['rows'][0],dat=bad);self.upload(s,s['rows'][1]);self.manager.start(s['id']);done=self.wait(s['id'])
        self.assertEqual(done['counts']['ERROR'],1);self.assertEqual(done['counts']['PASS'],1)
    def test_source_mutation_rejected(self):
        s=self.make();self.upload(s);p=self.root/'jobs'/s['id']/'items/r00001/record.dat';b=bytearray(p.read_bytes());b[8]^=1;p.write_bytes(b)
        self.manager.start(s['id']);done=self.wait(s['id']);self.assertEqual(done['verdict'],'ERROR');self.assertIn('哈希',done['rows'][0]['reason'])
    def test_completed_reject_cannot_overwrite(self):
        s=self.make();self.upload(s)
        with self.assertRaises(ValueError):self.manager.reject(s['id'],'r00001','fake error')
    def test_cancel_inflight_stops_remaining_only(self):
        import threading
        from esswave.engine import run as real_run
        entered=threading.Event();release=threading.Event()
        def slow(*args):entered.set();release.wait(2);return real_run(*args)
        s=self.make(names=('a','b'));[self.upload(s,r) for r in s['rows']]
        with patch('esswave.batchwork.run',side_effect=slow):
            self.manager.start(s['id']);self.assertTrue(entered.wait(2));self.manager.cancel(s['id']);release.set();done=self.wait(s['id'])
        self.assertEqual(done['counts']['PASS'],1);self.assertEqual(done['counts']['CANCELLED'],1);self.assertNotEqual(done['verdict'],'PASS')
    def test_report_html_escapes_paths(self):
        s=self.make(names=('<img src=x onerror=1>',));h=batch_index(s);self.assertNotIn('<img src=x',h);self.assertIn('&lt;img',h)
    def test_csv_formula_injection(self):
        s=self.make(names=('=1+1',));csv=csv_summary(s);self.assertIn("'=1+1.cfg",csv)
    def test_three_history_bound(self):
        ids=[]
        for i in range(4):
            s=self.make();ids.append(s['id']);self.manager.cancel(s['id'])
        self.assertEqual(len(self.manager.jobs),3)
        with self.assertRaises(ValueError):self.manager.snapshot(ids[0])
