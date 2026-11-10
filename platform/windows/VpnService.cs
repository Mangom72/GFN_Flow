using System;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Threading;

sealed class VpnService : ServiceBase
{
    const string PipeName = "GFNFlow.Vpn.v1";
    const string Done = "{\"type\":\"done\"}";
    const string Error = "{\"type\":\"error\",\"message\":\"The VPN service could not complete the request.\"}";
    const string Busy = "{\"type\":\"error\",\"message\":\"Another VPN action is running.\"}";
    const int FrameLimit = 32768;
    static readonly string Root = AppDomain.CurrentDomain.BaseDirectory;
    static readonly string Data = Path.Combine(Root, "data");
    static readonly string Cancel = Path.Combine(Data, "cancel");
    static readonly string Python = Path.Combine(Root, "python", "python.exe");
    static readonly string Worker = Path.Combine(Root, "app", "windows_vpn_service_worker.py");
    static readonly Encoding Utf8 = new UTF8Encoding(false);
    readonly ManualResetEvent stopping = new ManualResetEvent(false);
    readonly object operation = new object();
    readonly object cancellation = new object();
    bool scanning;
    Thread listener;
    SecurityIdentifier owner;

    VpnService() { ServiceName = "GFNFlowVpn"; CanStop = true; CanShutdown = true; }
    static void Main() { Run(new VpnService()); }

    protected override void OnStart(string[] args)
    {
        owner = new SecurityIdentifier(File.ReadAllText(Path.Combine(Root, "owner.sid")).Trim());
        stopping.Reset();
        listener = new Thread(Listen) { IsBackground = true, Name = "GFN VPN pipe" };
        listener.Start();
    }

    protected override void OnStop()
    {
        stopping.Set();
        lock (cancellation)
            if (scanning) File.WriteAllText(Cancel, "");
        if (listener != null) listener.Join(4000);
        if (Monitor.TryEnter(operation, 12000)) Monitor.Exit(operation);
    }

    protected override void OnShutdown() { OnStop(); }

    NamedPipeServerStream MakePipe()
    {
        var security = new PipeSecurity();
        security.SetAccessRuleProtection(true, false);
        security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid, null),
            PipeAccessRights.FullControl, AccessControlType.Deny));
        security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
            PipeAccessRights.FullControl, AccessControlType.Allow));
        security.AddAccessRule(new PipeAccessRule(owner,
            PipeAccessRights.Read | PipeAccessRights.WriteData | PipeAccessRights.Synchronize,
            AccessControlType.Allow));
        return new NamedPipeServerStream(PipeName, PipeDirection.InOut, 4, PipeTransmissionMode.Byte,
            PipeOptions.Asynchronous, 4096, 65536, security);
    }

    void Listen()
    {
        RunWorker('x', null);
        while (!stopping.WaitOne(0))
        {
            NamedPipeServerStream pipe = null;
            try
            {
                pipe = MakePipe();
                var pending = pipe.BeginWaitForConnection(null, null);
                using (var signal = pending.AsyncWaitHandle)
                {
                    if (WaitHandle.WaitAny(new[] { signal, stopping }) != 0) return;
                    pipe.EndWaitForConnection(pending);
                }
                var connected = pipe;
                ThreadPool.QueueUserWorkItem(_ => Serve(connected));
                pipe = null;
            }
            catch (IOException) { if (stopping.WaitOne(100)) return; }
            finally { if (pipe != null) pipe.Dispose(); }
        }
    }

    static bool Send(Stream stream, string line)
    {
        try
        {
            byte[] bytes = Utf8.GetBytes(line + "\n");
            var pending = stream.BeginWrite(bytes, 0, bytes.Length, null, null);
            using (var signal = pending.AsyncWaitHandle)
            {
                if (!signal.WaitOne(4000)) return false;
                stream.EndWrite(pending);
            }
            return true;
        }
        catch (IOException) { return false; }
        catch (ObjectDisposedException) { return false; }
    }

    static bool ReadExact(Stream stream, byte[] bytes, Stopwatch deadline)
    {
        int offset = 0;
        while (offset < bytes.Length)
        {
            int remaining = 3000 - (int)deadline.ElapsedMilliseconds;
            if (remaining <= 0) return false;
            var pending = stream.BeginRead(bytes, offset, bytes.Length - offset, null, null);
            using (var signal = pending.AsyncWaitHandle)
            {
                if (!signal.WaitOne(remaining)) return false;
                int count = stream.EndRead(pending);
                if (count == 0) return false;
                offset += count;
            }
        }
        return true;
    }

    void Serve(NamedPipeServerStream pipe)
    {
        using (pipe)
        try
        {
            var code = new byte[1];
            var deadline = Stopwatch.StartNew();
            if (!ReadExact(pipe, code, deadline)) return;
            if (stopping.WaitOne(0)) return;
            if (code[0] == 's')
            {
                var profiles = Path.Combine(Data, "MacroStudioNative", "vpn");
                Send(pipe, File.Exists(Path.Combine(profiles, "gfn-jp.conf")) ||
                    File.Exists(Path.Combine(profiles, "selected.json")) ? Done : Error);
                return;
            }
            if (code[0] == 'c')
            {
                lock (cancellation)
                {
                    if (scanning) File.WriteAllText(Cancel, "");
                    Send(pipe, scanning ? Done : "{\"type\":\"idle\"}");
                }
                return;
            }
            if (code[0] == 'p') { Send(pipe, RunWorker('p', null) ?? Error); return; }
            byte[] payload = null;
            if (code[0] == 'i' || code[0] == 'u')
            {
                var header = new byte[4];
                if (!ReadExact(pipe, header, deadline)) return;
                int length = BitConverter.ToInt32(header, 0);
                if (length < 1 || length > FrameLimit) { Send(pipe, Error); return; }
                payload = new byte[length];
                if (!ReadExact(pipe, payload, deadline)) return;
            }
            else if (code[0] != '0' && code[0] != '1' && code[0] != 'b')
            { Send(pipe, Error); return; }
            if (!Monitor.TryEnter(operation)) { Send(pipe, Busy); return; }
            string result = Error;
            try
            {
                if (code[0] == 'b')
                {
                    lock (cancellation)
                    {
                        File.Delete(Cancel);
                        scanning = true;
                    }
                }
                result = RunWorker((char)code[0], pipe, payload) ?? Error;
            }
            finally
            {
                try
                {
                    if (code[0] == 'b')
                    {
                        lock (cancellation) { scanning = false; File.Delete(Cancel); }
                        if (RunWorker('x', null) != "{\"type\": \"done\"}") result = Error;
                    }
                }
                finally { Monitor.Exit(operation); }
            }
            Send(pipe, result);
        }
        catch (IOException) { }
        catch (Exception) { Send(pipe, Error); }
    }

    static string RunWorker(char code, Stream stream, byte[] payload = null)
    {
        var info = new ProcessStartInfo(Python, "-I -B -X utf8 \"" + Worker + "\" " + code) {
            UseShellExecute = false, RedirectStandardOutput = true, CreateNoWindow = true,
            WorkingDirectory = Root, StandardOutputEncoding = Utf8, RedirectStandardInput = payload != null
        };
        info.EnvironmentVariables["PATH"] = Environment.SystemDirectory;
        using (var process = Process.Start(info))
        using (var timer = new Timer(_ => {
            try { if (!process.HasExited) process.Kill(); } catch (Exception) { }
        }, null, code == 'b' ? 240000 : 85000, Timeout.Infinite))
        {
            if (payload != null)
            {
                process.StandardInput.Write(Encoding.ASCII.GetString(payload));
                process.StandardInput.Close();
            }
            string result = null;
            string line;
            while ((line = process.StandardOutput.ReadLine()) != null)
            {
                if (!line.StartsWith("{\"type\": \"progress\"", StringComparison.Ordinal))
                {
                    result = line;
                    continue;
                }
                if (stream != null && !Send(stream, line))
                {
                    if (code == 'b') File.WriteAllText(Cancel, "");
                    stream = null;
                }
            }
            process.WaitForExit();
            return result;
        }
    }
}
