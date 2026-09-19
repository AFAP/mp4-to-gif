// MP4 -> transparent GIF/WebP : single-file self-extracting launcher.
//
// Layout of the produced exe:  [this launcher][zip payload][8-byte zip length][16-byte magic]
// The trailing metadata is read from the END of the file, so the launcher never
// needs to know its own size. Keep the magic in sync with scripts/build.ps1.
//
// Why not PyInstaller --onefile: that bootloader always unpacks into the system temp
// directory on EVERY launch, which is slow and can be blocked by security software.
// This launcher unpacks once into a data folder next to the exe (falling back to
// %LOCALAPPDATA% when the exe folder is read-only) and then just runs the app.
//
// Build with the .NET Framework compiler that ships with Windows:
//   csc /target:winexe /out:launcher.exe /r:System.IO.Compression.FileSystem.dll
//       /r:System.Windows.Forms.dll /r:System.Drawing.dll launcher.cs

using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace Mp4GifSfx
{
    internal sealed class SubStream : Stream
    {
        private readonly Stream _base;
        private readonly long _start;
        private readonly long _length;
        private long _pos;

        public SubStream(Stream b, long start, long length)
        {
            _base = b; _start = start; _length = length; _pos = 0;
        }

        public override bool CanRead { get { return true; } }
        public override bool CanSeek { get { return true; } }
        public override bool CanWrite { get { return false; } }
        public override long Length { get { return _length; } }
        public override long Position { get { return _pos; } set { _pos = value; } }

        public override int Read(byte[] buffer, int offset, int count)
        {
            long remain = _length - _pos;
            if (remain <= 0) return 0;
            if (count > remain) count = (int)remain;
            _base.Position = _start + _pos;
            int n = _base.Read(buffer, offset, count);
            _pos += n;
            return n;
        }

        public override long Seek(long offset, SeekOrigin origin)
        {
            long np = origin == SeekOrigin.Begin ? offset
                    : origin == SeekOrigin.Current ? _pos + offset
                    : _length + offset;
            _pos = np;
            return _pos;
        }

        public override void Flush() { }
        public override void SetLength(long value) { throw new NotSupportedException(); }
        public override void Write(byte[] b, int o, int c) { throw new NotSupportedException(); }
    }

    internal sealed class ProgressForm : Form
    {
        private readonly ProgressBar _bar;
        private readonly Label _label;

        public ProgressForm()
        {
            Text = "MP4 -> GIF / WebP";
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterScreen;
            MaximizeBox = false; MinimizeBox = false;
            ClientSize = new Size(420, 110);
            Font = new Font("Microsoft YaHei UI", 9f);
            TopMost = true;

            _label = new Label();
            _label.Text = "首次运行，正在解压运行文件……";
            _label.AutoSize = false;
            _label.SetBounds(18, 16, 384, 22);
            Controls.Add(_label);

            _bar = new ProgressBar();
            _bar.SetBounds(18, 46, 384, 18);
            _bar.Minimum = 0; _bar.Maximum = 100;
            Controls.Add(_bar);
        }

        public void SetProgress(int done, int total)
        {
            int pct = total <= 0 ? 0 : (int)(done * 100L / total);
            if (pct > 100) pct = 100;
            _bar.Value = pct;
            _label.Text = "首次运行，正在解压运行文件…… " + pct + "%";
            Application.DoEvents();
        }

        public void SetText(string s) { _label.Text = s; Application.DoEvents(); }
    }

    internal static class Program
    {
        private const string Magic = "MP4GIF-SFX-v1.0!";   // exactly 16 bytes
        private const string DataDirName = "mp4togif_运行文件";
        private const string AppExeName = "mp4togif.exe";

        private static string _logPath = "";

        private static void Log(string s)
        {
            if (_logPath.Length == 0) return;
            try { File.AppendAllText(_logPath, DateTime.Now.ToString("HH:mm:ss") + "  " + s + "\r\n"); }
            catch (Exception) { }
        }

        private static int Fail(string message)
        {
            Log("FAIL: " + message);
            try
            {
                MessageBox.Show(message, "MP4 -> GIF / WebP",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            catch (Exception) { }
            return 1;
        }

        [STAThread]
        private static int Main(string[] args)
        {
            Application.EnableVisualStyles();
            string self;
            try
            {
                self = Process.GetCurrentProcess().MainModule.FileName;
            }
            catch (Exception)
            {
                self = Assembly.GetExecutingAssembly().Location;
            }
            try
            {
                // 只在出错时才写这个文件，正常运行不在用户目录里留垃圾
                _logPath = Path.Combine(Path.GetDirectoryName(self), "单文件启动日志.txt");
            }
            catch (Exception) { _logPath = ""; }

            long zipLen, zipOff;
            try
            {
                using (FileStream fs = File.OpenRead(self))
                {
                    if (fs.Length < 24 + 1024) return Fail("文件不完整，请重新拷贝。");
                    fs.Seek(fs.Length - 24, SeekOrigin.Begin);
                    byte[] meta = new byte[24];
                    int got = 0;
                    while (got < 24)
                    {
                        int n = fs.Read(meta, got, 24 - got);
                        if (n <= 0) break;
                        got += n;
                    }
                    zipLen = BitConverter.ToInt64(meta, 0);
                    string magic = Encoding.ASCII.GetString(meta, 8, 16);
                    if (magic != Magic) return Fail("没有找到内置数据，文件可能被修改过。");
                    zipOff = fs.Length - 24 - zipLen;
                    if (zipOff <= 0) return Fail("文件不完整。");
                }
            }
            catch (Exception ex)
            {
                return Fail("读取自身失败：" + ex.Message);
            }

            string exeDir = Path.GetDirectoryName(self);
            string target = Path.Combine(exeDir, DataDirName);

            // exe 所在目录可能只读（比如放在 Program Files），退回用户目录
            if (!TryPrepare(target))
            {
                target = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "mp4togif");
                if (!TryPrepare(target)) return Fail("无法创建运行目录：" + target);
            }

            string appExe = Path.Combine(target, AppExeName);
            string stamp = Path.Combine(target, ".payload");
            string want = zipLen.ToString();

            if (!File.Exists(appExe) || ReadStamp(stamp) != want)
            {
                // No popup for command-line runs, and a missing desktop (CI, service
                // session) must never break the extraction - the bar is a nicety.
                ProgressForm form = null;
                if (args == null || args.Length == 0)
                {
                    try { form = new ProgressForm(); form.Show(); }
                    catch (Exception) { form = null; }
                }
                try
                {
                    Extract(self, zipOff, zipLen, target, form);
                    File.WriteAllText(stamp, want);
                }
                catch (Exception ex)
                {
                    if (form != null) { form.Hide(); form.Dispose(); }
                    return Fail("解压失败：" + ex.Message);
                }
                if (form != null) { form.Hide(); form.Dispose(); }
            }

            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(appExe);
                // Inherit the caller's working directory so relative --cli paths resolve
                // where the user actually is; fall back to the runtime folder.
                string cwd = null;
                try { cwd = Environment.CurrentDirectory; }
                catch (Exception) { cwd = null; }
                psi.WorkingDirectory = (cwd != null && Directory.Exists(cwd)) ? cwd : target;
                psi.UseShellExecute = false;
                if (args != null && args.Length > 0)
                {
                    StringBuilder sb = new StringBuilder();
                    for (int i = 0; i < args.Length; i++)
                    {
                        if (i > 0) sb.Append(' ');
                        sb.Append('"').Append(args[i].Replace("\"", "\\\"")).Append('"');
                    }
                    psi.Arguments = sb.ToString();
                }
                Process child = Process.Start(psi);
                // With arguments (typically --cli) the caller is a script or a CI step
                // that needs the real exit code, so wait for the app and pass it on.
                // Without arguments this is a plain GUI launch: return immediately.
                if (child != null && args != null && args.Length > 0)
                {
                    child.WaitForExit();
                    return child.ExitCode;
                }
                return 0;
            }
            catch (Exception ex)
            {
                return Fail("启动失败：" + ex.Message);
            }
        }

        private static bool TryPrepare(string dir)
        {
            try
            {
                Directory.CreateDirectory(dir);
                string probe = Path.Combine(dir, ".wtest");
                File.WriteAllText(probe, "x");
                File.Delete(probe);
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static string ReadStamp(string path)
        {
            try { return File.Exists(path) ? File.ReadAllText(path).Trim() : ""; }
            catch (Exception) { return ""; }
        }

        private static void Extract(string self, long off, long len, string target, ProgressForm form)
        {
            using (FileStream fs = File.OpenRead(self))
            using (SubStream ss = new SubStream(fs, off, len))
            using (ZipArchive za = new ZipArchive(ss, ZipArchiveMode.Read))
            {
                int total = za.Entries.Count;
                int done = 0;
                foreach (ZipArchiveEntry e in za.Entries)
                {
                    string name = e.FullName.Replace('/', Path.DirectorySeparatorChar);
                    string dest = Path.Combine(target, name);
                    if (string.IsNullOrEmpty(e.Name))
                    {
                        Directory.CreateDirectory(dest);
                    }
                    else
                    {
                        string parent = Path.GetDirectoryName(dest);
                        if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                        e.ExtractToFile(dest, true);
                    }
                    done++;
                    if (form != null && (done % 3 == 0 || done == total)) form.SetProgress(done, total);
                }
            }
        }
    }
}
