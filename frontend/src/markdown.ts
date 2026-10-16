import MarkdownIt from 'markdown-it'
const markdown = new MarkdownIt({ html: false, linkify: false, breaks: true })
// 不加载模型生成的远程图片，避免来源正文被编码进第三方请求。
markdown.disable('image')
const defaultValidate = markdown.validateLink.bind(markdown)
markdown.validateLink = (url: string) => /^https?:\/\//i.test(url) && defaultValidate(url)
const linkOpen = markdown.renderer.rules.link_open
markdown.renderer.rules.link_open = (tokens, index, options, env, self) => {
  tokens[index]!.attrSet('target', '_blank')
  tokens[index]!.attrSet('rel', 'noopener noreferrer')
  tokens[index]!.attrSet('referrerpolicy', 'no-referrer')
  return linkOpen ? linkOpen(tokens, index, options, env, self) : self.renderToken(tokens, index, options)
}
export function renderMarkdown(content: string) { return markdown.render(content) }
